#!/usr/bin/env bash
# Adds two HTTPS proxy listeners to existing RU Nginx, via REALITY to two US backends.
# Does not change old RU:443, RU:43127, US server or user accounts.
set -Eeuo pipefail
RU=129.101.115.213; US=186.246.18.103; XRAY=/opt/vesya-mask/bin/xray
CONF=/etc/vesya-universal/ru.json; PEER=/root/vesya-universal-peer.json
NGINX=/etc/nginx/ru-guest-gateway.conf
CERT=/etc/letsencrypt/live/ru.paruslegal.ru
UNIT=vesya-universal-ru.service

[[ $EUID -eq 0 ]] || { echo "ERROR: root required";exit 1; }
ip -4 -o addr show | grep -Fq "$RU/" || { echo "ERROR: wrong VPS, expected RU $RU";exit 1; }
[[ -f "$NGINX" && -f "$CERT/fullchain.pem" && -f "$CERT/privkey.pem" ]] || {
 echo "ERROR: RU HTTPS gateway/certificate missing";exit 1;
}
if [[ -f "$CONF" ]] && systemctl is-active --quiet "$UNIT" &&
   grep -Fq "listen 0.0.0.0:4453 ssl;" "$NGINX" &&
   grep -Fq "listen 0.0.0.0:4454 ssl;" "$NGINX"; then
 echo "ALREADY INSTALLED: Universal RU mode active";exit 0
fi
for port in 4453 4454; do
 if ! grep -Fq "listen 0.0.0.0:$port ssl;" "$NGINX" &&
    ss -H -lnt "( sport = :$port )" | grep -q .; then
  echo "ERROR: public port $port already taken";exit 1
 fi
done
for port in 43141 43142; do
 if [[ ! -f "$CONF" ]] && ss -H -lnt "( sport = :$port )" | grep -q .; then
  echo "ERROR: local port $port already taken";exit 1
 fi
done

if [[ ! -s "$PEER" ]]; then
 echo "Copying secret connection data from US over SSH (US root password may be needed)"
 scp -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new \
   "root@$US:/root/vesya-universal-peer.json" "$PEER.partial"
 chmod 0600 "$PEER.partial";mv "$PEER.partial" "$PEER"
fi
if [[ ! -x "$XRAY" ]]; then
 tmp=$(mktemp -d);trap 'rm -rf "$tmp"' EXIT
 curl -fL --retry 2 --connect-timeout 15 --max-time 120 \
  "https://github.com/XTLS/Xray-core/releases/download/v26.11.9/Xray-linux-64.zip" -o "$tmp/xray.zip"
 python3 - "$tmp/xray.zip" "$tmp/xray" <<'PY'
import zipfile,sys
with zipfile.ZipFile(sys.argv[1]) as z:
 b=z.read("xray")
 assert len(b)>1000000 and b[:4]==b"\x7fELF"
 open(sys.argv[2],"wb").write(b)
PY
 install -d -m 0755 /opt/vesya-mask/bin
 install -m 0755 "$tmp/xray" "$XRAY"
fi
id vesyamask >/dev/null 2>&1 || useradd --system --user-group --no-create-home --shell /usr/sbin/nologin vesyamask
install -d -o root -g vesyamask -m 0750 /etc/vesya-universal

PEER="$PEER" CONF="$CONF" US="$US" python3 - <<'PY'
import json,os,uuid,pathlib
p=json.loads(pathlib.Path(os.environ["PEER"]).read_text())
assert p["host"]==os.environ["US"] and p["port"]==24446
assert p["serverName"]=="www.microsoft.com" and len(p["shortId"])==16
for k in ("socks","vesya"):uuid.UUID(p["modes"][k])
inbounds=[{"tag":k+"-entry","listen":"127.0.0.1","port":port,
 "protocol":"http","settings":{"users":[],"allowTransparent":False}}
 for k,port in (("socks",43141),("vesya",43142))]
outbounds=[{"tag":k+"-us","protocol":"vless",
 "settings":{"address":p["host"],"port":p["port"],"id":p["modes"][k],
 "encryption":"none","flow":"xtls-rprx-vision"},
 "streamSettings":{"network":"tcp","security":"reality","realitySettings":{
 "serverName":p["serverName"],"fingerprint":"chrome",
 "password":p["password"],"shortId":p["shortId"],"spiderX":""}}}
 for k in ("socks","vesya")]
routes=[{"type":"field","inboundTag":[k+"-entry"],"outboundTag":k+"-us"}
 for k in ("socks","vesya")]
out={"log":{"loglevel":"warning"},"inbounds":inbounds,"outbounds":outbounds,"routing":{"rules":routes}}
f=pathlib.Path(os.environ["CONF"]);f.write_text(json.dumps(out,indent=2)+"\n")
f.chmod(0o640)
PY
chown root:vesyamask "$CONF"
"$XRAY" run -test -c "$CONF"
cat > /etc/systemd/system/vesya-universal-ru.service <<EOF
[Unit]
Description=Vesya Universal two-mode RU/US REALITY bridge
After=network-online.target
Wants=network-online.target
[Service]
User=vesyamask
Group=vesyamask
ExecStart=$XRAY run -c $CONF
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
systemctl is-active --quiet "$UNIT" || {
 echo "ERROR: RU Xray failed, Nginx unchanged"
 journalctl -u "$UNIT" -n 30 --no-pager;exit 1
}

for localport in 43141 43142; do
 ip=$(curl -4fsS -x "http://127.0.0.1:$localport" --connect-timeout 12 --max-time 40 https://api.ipify.org/ || true)
 if [[ "$ip" != "$US" ]]; then
  echo "ERROR: mode $localport not routed through US. Got: $ip. Public Nginx unchanged."
  journalctl -u "$UNIT" -n 20 --no-pager
  exit 1
 fi
 echo "OK: local mode $localport exits through $ip"
done
BACKUP="$NGINX.before-universal-$(date +%Y%m%d-%H%M%S)"
cp -p "$NGINX" "$BACKUP"
NGINX="$NGINX" CERT="$CERT" python3 - <<'PY'
from pathlib import Path
import os
p=Path(os.environ["NGINX"]);s=p.read_text()
assert s.count("stream {")==1 and s.rstrip().endswith("}"),"Unexpected Nginx config"
for public,local in ((4453,43141),(4454,43142)):
 if f"listen 0.0.0.0:{public} ssl;" in s:continue
 block="""
    # Vesya universal personal HTTPS proxy, no authentication
    server {
        listen 0.0.0.0:%d ssl;
        ssl_certificate %s/fullchain.pem;
        ssl_certificate_key %s/privkey.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
        proxy_pass 127.0.0.1:%d;
        proxy_connect_timeout 15s;
        proxy_timeout 1h;
    }
"""%(public,os.environ["CERT"],os.environ["CERT"],local)
 i=s.rfind("}")
 s=s[:i]+block+s[i:]
p.write_text(s)
PY
if ! nginx -t || ! systemctl reload nginx; then
 echo "ERROR: reverting Nginx config, universal listeners not activated"
 cp -p "$BACKUP" "$NGINX"
 nginx -t && systemctl reload nginx || true
 exit 1
fi
echo "SUCCESS: RU universal proxy listeners ready: HTTPS 4453=SOCKS5, 4454=3proxy."
echo "Both use REALITY to US. Open inbound TCP 4453/4454 in Timeweb firewall."
echo "WARNING: public no-auth HTTPS forward proxies, anyone knowing endpoints can use them."
