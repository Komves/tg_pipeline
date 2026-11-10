#!/usr/bin/env bash
# Add separate US REALITY egress for HTTPS front on RU. Existing proxies untouched.
set -Eeuo pipefail
US_IP=186.246.18.103; PORT=24444; VERSION=v26.11.9
XRAY=/opt/vesya-mask/bin/xray; BASE=/etc/vesya-mask
CONFIG=$BASE/https-us.json; PEER=/root/vesya-https-peer.json; UNIT=vesya-https-us.service
[[ $EUID -eq 0 ]] || { echo "ERROR: root required"; exit 1; }
ip -4 -o addr show | grep -Fq "$US_IP/" || { echo "ERROR: run on US $US_IP"; exit 1; }
if [[ -s "$CONFIG" && -s "$PEER" ]]; then
  echo "Previously configured. Status: $(systemctl is-active "$UNIT" || true)"
  exit 0
fi
[[ ! -e "$CONFIG" && ! -e "$PEER" ]] || { echo "ERROR: partial configuration"; exit 1; }
! ss -H -lnt "( sport = :$PORT )" | grep -q . || { echo "ERROR: port $PORT busy"; exit 1; }
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
XRAY="$XRAY" CONFIG="$CONFIG" PEER="$PEER" US_IP="$US_IP" PORT="$PORT" python3 - <<'PY'
import json,os,re,secrets,subprocess,uuid,pathlib
s=subprocess.run([os.environ["XRAY"],"x25519"],capture_output=True,text=True,check=True).stdout
a=re.search(r"(?im)^\s*(?:PrivateKey|Private key)\s*:\s*(\S+)",s)
b=re.search(r"(?im)^\s*(?:Password(?:\s*\([^)]*\))?|PublicKey|Public key)\s*:\s*(\S+)",s)
if not a or not b:raise SystemExit("Cannot parse x25519 output")
uid=str(uuid.uuid4());sid=secrets.token_hex(8)
data={"log":{"loglevel":"warning"},
 "inbounds":[{"listen":"0.0.0.0","port":int(os.environ["PORT"]),"protocol":"vless",
 "settings":{"clients":[{"id":uid,"flow":"xtls-rprx-vision"}],"decryption":"none"},
 "streamSettings":{"network":"tcp","security":"reality","realitySettings":{
 "target":"www.microsoft.com:443","serverNames":["www.microsoft.com"],
 "privateKey":a.group(1),"shortIds":[sid]}}}],
 "outbounds":[{"protocol":"freedom","tag":"us-direct","settings":{
 "finalRules":[{"action":"block","network":"tcp","port":"22,25,465,587,3306,3389"}]}}]}
peer={"host":os.environ["US_IP"],"port":int(os.environ["PORT"]),
 "id":uid,"serverName":"www.microsoft.com","password":b.group(1),"shortId":sid}
c=pathlib.Path(os.environ["CONFIG"]);c.write_text(json.dumps(data,indent=2)+"\n");c.chmod(0o640)
p=pathlib.Path(os.environ["PEER"]);p.write_text(json.dumps(peer,indent=2)+"\n");p.chmod(0o600)
PY
chown root:vesyamask "$CONFIG"
"$XRAY" run -test -c "$CONFIG"
cat > "/etc/systemd/system/$UNIT" <<EOF
[Unit]
Description=Isolated REALITY exit for RU HTTPS proxy
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
 echo "ERROR: US service failed; originals untouched"
 journalctl -u "$UNIT" -n 20 --no-pager
 exit 1
fi
echo "SUCCESS: US REALITY $US_IP:$PORT"
echo "Next run RU installer. Do not show peer credentials in chat."
