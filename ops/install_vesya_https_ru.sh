#!/usr/bin/env bash
# Separate HTTPS forward proxy RU:4443, REALITY US:24444, no auth.
# RU:443 and all existing proxy ports are never changed.
set -Eeuo pipefail
RU_IP=129.101.115.213; US_IP=186.246.18.103; US_PORT=24444
PUBLIC_PORT=4443; LOCAL_PORT=43139; VERSION=v26.11.9
XRAY=/opt/vesya-mask/bin/xray; BASE=/etc/vesya-mask
CONFIG=$BASE/https-ru.json; PEER=/root/vesya-https-peer.json; UNIT=vesya-https-ru.service
NGINX=/etc/nginx/ru-guest-gateway.conf; CERT=/etc/letsencrypt/live/ru.paruslegal.ru
[[ $EUID -eq 0 ]] || { echo "ERROR: root required"; exit 1; }
ip -4 -o addr show | grep -Fq "$RU_IP/" || { echo "ERROR: run on RU $RU_IP"; exit 1; }
[[ -f "$NGINX" && -f "$CERT/fullchain.pem" && -f "$CERT/privkey.pem" ]] || {
 echo "ERROR: missing RU Nginx or certificate; no changes"; exit 1;
}
if ! grep -Fq "listen 0.0.0.0:$PUBLIC_PORT ssl;" "$NGINX"; then
 ! ss -H -lnt "( sport = :$PUBLIC_PORT )" | grep -q . || { echo "ERROR: public port busy"; exit 1; }
fi
if [[ ! -f "$CONFIG" ]]; then
 ! ss -H -lnt "( sport = :$LOCAL_PORT )" | grep -q . || { echo "ERROR: local port busy"; exit 1; }
fi
if [[ ! -s "$PEER" ]]; then
 echo "Copying credentials from US over SSH (enter US root SSH password if asked)..."
 scp -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new \
   "root@$US_IP:/root/vesya-https-peer.json" "$PEER.tmp"
 chmod 600 "$PEER.tmp"; mv "$PEER.tmp" "$PEER"
fi
if [[ ! -x "$XRAY" ]]; then
 tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
 curl -fL --retry 2 --connect-timeout 15 --max-time 120 \
   "https://github.com/XTLS/Xray-core/releases/download/$VERSION/Xray-linux-64.zip" -o "$tmp/xray.zip"
 python3 - "$tmp/xray.zip" "$tmp/xray" <<'PY'
import zipfile,sys
with zipfile.ZipFile(sys.argv[1]) as z:
 b=z.read('xray');assert len(b)>1000000 and b[:4]==b'\x7fELF'
 open(sys.argv[2],'wb').write(b)
PY
 install -d -m 0755 /opt/vesya-mask/bin
 install -m 0755 "$tmp/xray" "$XRAY"
fi
"$XRAY" version | head -1
id vesyamask >/dev/null 2>&1 || useradd --system --user-group --no-create-home --shell /usr/sbin/nologin vesyamask
install -d -o root -g vesyamask -m 0750 "$BASE"
PEER="$PEER" CONFIG="$CONFIG" US_IP="$US_IP" US_PORT="$US_PORT" LOCAL_PORT="$LOCAL_PORT" python3 - <<'PY'
import os,json,pathlib,uuid
p=json.loads(pathlib.Path(os.environ["PEER"]).read_text())
assert p["host"]==os.environ["US_IP"] and p["port"]==int(os.environ["US_PORT"])
assert p["serverName"]=="www.microsoft.com" and len(p["shortId"])==16 and p["password"]
uuid.UUID(p["id"])
data={"log":{"loglevel":"warning"},
 "inbounds":[{"listen":"127.0.0.1","port":int(os.environ["LOCAL_PORT"]),
 "protocol":"http","settings":{"users":[],"allowTransparent":False}}],
 "outbounds":[{"tag":"masked-us","protocol":"vless",
 "settings":{"address":p["host"],"port":p["port"],"id":p["id"],
 "encryption":"none","flow":"xtls-rprx-vision"},
 "streamSettings":{"network":"tcp","security":"reality","realitySettings":{
 "serverName":p["serverName"],"fingerprint":"chrome",
 "password":p["password"],"shortId":p["shortId"],"spiderX":""}}}]}
f=pathlib.Path(os.environ["CONFIG"]);f.write_text(json.dumps(data,indent=2)+"\n");f.chmod(0o640)
PY
chown root:vesyamask "$CONFIG"
"$XRAY" run -test -c "$CONFIG"
cat > "/etc/systemd/system/$UNIT" <<EOF
[Unit]
Description=Isolated RU HTTPS front -> US REALITY
After=network-online.target
Wants=network-online.target
[Service]
User=vesyamask
Group=vesyamask
ExecStart=$XRAY run -c $CONFIG
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
LimitNOFILE=65536
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now "$UNIT"
sleep 2
if ! systemctl is-active --quiet "$UNIT"; then
 echo "ERROR: RU service not active; existing Nginx unchanged"
 journalctl -u "$UNIT" -n 20 --no-pager; exit 1
fi
echo "Checking new REALITY route before exposing HTTPS port..."
GOT=$(curl -4fsS -x "http://127.0.0.1:$LOCAL_PORT" \
 --connect-timeout 10 --max-time 30 https://api.ipify.org/ || true)
if [[ "$GOT" != "$US_IP" ]]; then
 echo "ERROR: tunnel not confirmed, got: $GOT (Nginx unchanged)"; exit 1
fi
if ! grep -Fq "listen 0.0.0.0:$PUBLIC_PORT ssl;" "$NGINX"; then
 BACKUP="$NGINX.before-https-$(date +%Y%m%d-%H%M%S)"
 cp -p "$NGINX" "$BACKUP"
 NGINX="$NGINX" PUBLIC_PORT="$PUBLIC_PORT" LOCAL_PORT="$LOCAL_PORT" CERT="$CERT" python3 - <<'PY'
from pathlib import Path
import os
p=Path(os.environ["NGINX"]);s=p.read_text()
assert s.count("stream {")==1 and s.rstrip().endswith("}"),"Unexpected Nginx layout"
block="""
    # Vesya isolated no-auth HTTPS proxy pilot
    server {
        listen 0.0.0.0:%s ssl;
        ssl_certificate %s/fullchain.pem;
        ssl_certificate_key %s/privkey.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
        proxy_pass 127.0.0.1:%s;
        proxy_connect_timeout 10s;
        proxy_timeout 1h;
    }
"""%(os.environ["PUBLIC_PORT"],os.environ["CERT"],os.environ["CERT"],os.environ["LOCAL_PORT"])
i=s.rfind("}");p.write_text(s[:i]+block+s[i:])
PY
 if ! nginx -t; then
   echo "ERROR: nginx validation failed; restoring previous configuration"
   cp -p "$BACKUP" "$NGINX"
   nginx -t || true
   exit 1
 fi
 if ! systemctl reload nginx; then
   echo "ERROR: Nginx reload failed; reverting configuration"
   cp -p "$BACKUP" "$NGINX"
   nginx -t && systemctl reload nginx || true
   exit 1
 fi
fi
echo "SUCCESS: Chrome HTTPS ru.paruslegal.ru:$PUBLIC_PORT -> REALITY -> US $GOT"
echo "External check: curl -x https://ru.paruslegal.ru:$PUBLIC_PORT https://api.ipify.org"
echo "WARNING: public unauthenticated proxy, allow inbound TCP $PUBLIC_PORT in firewall if needed."
