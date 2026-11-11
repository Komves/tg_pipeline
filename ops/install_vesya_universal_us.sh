#!/usr/bin/env bash
# Personal, unlimited-duration proxy backend. Only NEW services; NEVER touches Vesya guest DB/service.
set -Eeuo pipefail
US_IP=186.246.18.103; REALITY_PORT=24446; LOCAL_HTTP_PORT=43145
VERSION=v26.11.9; XRAY=/opt/vesya-mask/bin/xray
ROOT=/etc/vesya-universal; PEER=/root/vesya-universal-peer.json
US_CONFIG=$ROOT/us.json
US_SERVICE=vesya-universal-us.service
HTTP_SERVICE=vesya-universal-http.service

[[ $EUID -eq 0 ]] || { echo "ERROR: root required"; exit 1; }
ip -4 -o addr show | grep -Fq "$US_IP/" || { echo "ERROR: this is not the US VPS $US_IP"; exit 1; }
for s in "$US_SERVICE" "$HTTP_SERVICE"; do
 if systemctl is-active --quiet "$s"; then
   echo "$s already active"
 fi
done
if [[ -f "$US_CONFIG" && -f "$PEER" ]] && systemctl is-active --quiet "$US_SERVICE" && systemctl is-active --quiet "$HTTP_SERVICE"; then
 echo "ALREADY INSTALLED: US server ready. No changes."
 exit 0
fi
[[ ! -e "$US_CONFIG" && ! -e "$PEER" ]] || { echo "ERROR: partial previous installation, inspect $ROOT first"; exit 1; }
for port in "$REALITY_PORT" "$LOCAL_HTTP_PORT"; do
 ! ss -H -lnt "( sport = :$port )" | grep -q . || { echo "ERROR: port $port occupied"; exit 1; }
done
if ! timeout 7 bash -c "exec 9<>/dev/tcp/127.0.0.1/43127" 2>/dev/null; then
 echo "ERROR: existing personal SOCKS5 on US port 43127 is not reachable"; exit 1
fi
THREEPROXY=
for candidate in "$(command -v 3proxy || true)" /usr/local/bin/3proxy /usr/bin/3proxy /opt/3proxy/bin/3proxy /usr/local/3proxy/bin/3proxy; do
 if [[ -n "$candidate" && -x "$candidate" ]]; then THREEPROXY="$candidate";break;fi
done
[[ -n "$THREEPROXY" ]] || { echo "ERROR: 3proxy binary not found; existing Vesya services NOT modified"; exit 1; }

if [[ ! -x "$XRAY" ]]; then
 echo "Installing separate Xray-core $VERSION (not modifying existing proxy)"
 temp=$(mktemp -d);trap 'rm -rf "$temp"' EXIT
 curl -fL --retry 2 --connect-timeout 15 --max-time 120 \
   "https://github.com/XTLS/Xray-core/releases/download/$VERSION/Xray-linux-64.zip" -o "$temp/xray.zip"
 python3 - "$temp/xray.zip" "$temp/xray" <<'PY'
import zipfile,sys
with zipfile.ZipFile(sys.argv[1]) as z:
 b=z.read("xray")
 assert len(b)>1000000 and b[:4]==b"\x7fELF"
 open(sys.argv[2],"wb").write(b)
PY
 install -d -m 0755 /opt/vesya-mask/bin
 install -m 0755 "$temp/xray" "$XRAY"
fi
"$XRAY" version | head -1
id vesyamask >/dev/null 2>&1 || useradd --system --user-group --no-create-home --shell /usr/sbin/nologin vesyamask
install -d -o root -g vesyamask -m 0750 "$ROOT"

# Standalone personal 3proxy, loopback ONLY. No 30-day Vesya guest account limits.
cat > "$ROOT/3proxy-personal.cfg" <<EOF
auth none
proxy -p$LOCAL_HTTP_PORT -i127.0.0.1
EOF
chmod 0640 "$ROOT/3proxy-personal.cfg"
chown root:vesyamask "$ROOT/3proxy-personal.cfg"
cat > "/etc/systemd/system/$HTTP_SERVICE" <<EOF
[Unit]
Description=Vesya Universal personal 3proxy (local-only, no expiry)
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=vesyamask
Group=vesyamask
ExecStart=$THREEPROXY $ROOT/3proxy-personal.cfg
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
LimitNOFILE=65536
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now "$HTTP_SERVICE"
sleep 2
systemctl is-active --quiet "$HTTP_SERVICE" || {
 echo "ERROR: personal HTTP 3proxy failed";journalctl -u "$HTTP_SERVICE" -n 25 --no-pager;exit 1
}
if [[ "$(curl -4fsS -x "http://127.0.0.1:$LOCAL_HTTP_PORT" --max-time 15 https://api.ipify.org/ || true)" != "$US_IP" ]]; then
 echo "ERROR: local 3proxy test failed; Xray not installed";exit 1
fi

XRAY="$XRAY" US_CONFIG="$US_CONFIG" PEER="$PEER" US_IP="$US_IP" REALITY_PORT="$REALITY_PORT" LOCAL_HTTP_PORT="$LOCAL_HTTP_PORT" python3 - <<'PY'
import json,os,re,secrets,subprocess,uuid,pathlib
s=subprocess.run([os.environ["XRAY"],"x25519"],capture_output=True,text=True,check=True).stdout
priv=re.search(r"(?im)^\s*(?:PrivateKey|Private key)\s*:\s*(\S+)",s)
pub=re.search(r"(?im)^\s*(?:Password(?:\s*\([^)]*\))?|PublicKey|Public key)\s*:\s*(\S+)",s)
if not priv or not pub:raise SystemExit("Cannot read REALITY keys from Xray")
socks_id=str(uuid.uuid4());vesya_id=str(uuid.uuid4());sid=secrets.token_hex(8)
data={
 "log":{"loglevel":"warning"},
 "inbounds":[{"tag":"reality",
  "listen":"0.0.0.0","port":int(os.environ["REALITY_PORT"]),"protocol":"vless",
  "settings":{"decryption":"none","clients":[
    {"id":socks_id,"email":"personal-socks@vesya.local","flow":"xtls-rprx-vision"},
    {"id":vesya_id,"email":"personal-http@vesya.local","flow":"xtls-rprx-vision"}
  ]},
  "streamSettings":{"network":"tcp","security":"reality","realitySettings":{
   "target":"www.microsoft.com:443","serverNames":["www.microsoft.com"],
   "privateKey":priv.group(1),"shortIds":[sid]}}}],
 "outbounds":[
  {"tag":"deny","protocol":"blackhole"},
  {"tag":"personal-socks","protocol":"socks","settings":{"address":"127.0.0.1","port":43127}},
  {"tag":"personal-http","protocol":"http","settings":{"address":"127.0.0.1","port":int(os.environ["LOCAL_HTTP_PORT"])}}
 ],
 "routing":{"rules":[
  {"type":"field","user":["personal-socks@vesya.local"],"outboundTag":"personal-socks"},
  {"type":"field","user":["personal-http@vesya.local"],"outboundTag":"personal-http"}
 ]}
}
peer={"host":os.environ["US_IP"],"port":int(os.environ["REALITY_PORT"]),
 "password":pub.group(1),"shortId":sid,"serverName":"www.microsoft.com",
 "modes":{"socks":socks_id,"vesya":vesya_id}}
f=pathlib.Path(os.environ["US_CONFIG"]);f.write_text(json.dumps(data,indent=2)+"\n");f.chmod(0o640)
p=pathlib.Path(os.environ["PEER"]);p.write_text(json.dumps(peer,indent=2)+"\n");p.chmod(0o600)
PY
chown root:vesyamask "$US_CONFIG"
"$XRAY" run -test -c "$US_CONFIG"
cat > "/etc/systemd/system/$US_SERVICE" <<EOF
[Unit]
Description=Vesya Universal US REALITY backend (SOCKS5 + dedicated 3proxy)
After=network-online.target $HTTP_SERVICE
Requires=$HTTP_SERVICE
Wants=network-online.target
[Service]
User=vesyamask
Group=vesyamask
ExecStart=$XRAY run -c $US_CONFIG
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
systemctl enable --now "$US_SERVICE"
sleep 2
systemctl is-active --quiet "$US_SERVICE" || {
 echo "ERROR: US REALITY service failed";journalctl -u "$US_SERVICE" -n 30 --no-pager;exit 1
}
echo "SUCCESS: US universal mode ready on TCP $REALITY_PORT"
echo "Personal SOCKS5: 127.0.0.1:43127; personal 3proxy: 127.0.0.1:$LOCAL_HTTP_PORT"
echo "No expiry on personal 3proxy. Original guest DB/API and existing public ports unchanged."
echo "Do NOT share $PEER (private access credentials)."
