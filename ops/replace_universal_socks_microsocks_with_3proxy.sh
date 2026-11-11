#!/usr/bin/env bash
# US only: replace broken microsocks backend of the NEW universal SOCKS5 mode
# with a separate loopback-only 3proxy SOCKS5. Do not touch guest proxy or microsocks.
set -Eeuo pipefail
IP=186.246.18.103
PORT=43146
UNIT=vesya-universal-socks.service
XUNIT=vesya-universal-us.service
CFG=/etc/vesya-universal/us.json
SOCKS_CFG=/etc/vesya-universal/3proxy-socks.cfg
HTTP_UNIT=/etc/systemd/system/vesya-universal-http.service
XRAY=/opt/vesya-mask/bin/xray

[[ $EUID -eq 0 ]] || { echo "ERROR: root required"; exit 1; }
# Enumerate every IPv4 address first. Avoid grep -q in a pipefail pipeline:
# it may close early (SIGPIPE), falsely rejecting a multi-address server.
IP_ADDRESSES=$(ip -4 -o addr show | awk '{split($4, a, "/"); print a[1]}')
if ! printf '%s\n' "$IP_ADDRESSES" | grep -Fx "$IP" >/dev/null; then
  echo "ERROR: wrong server; expected US $IP"
  echo "Local IPv4 addresses:"
  printf '%s\n' "$IP_ADDRESSES"
  exit 1
fi
[[ -s "$CFG" && -f "$HTTP_UNIT" && -x "$XRAY" ]] || { echo "ERROR: universal US installation missing"; exit 1; }
systemctl is-active --quiet "$XUNIT" || { echo "ERROR: universal US Xray not active"; exit 1; }
systemctl is-active --quiet vesya-universal-http.service || { echo "ERROR: dedicated HTTP 3proxy not active"; exit 1; }

CURRENT=$(python3 - "$CFG" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
s=[x for x in d["outbounds"] if x.get("tag")=="personal-socks"]
assert len(s)==1 and s[0]["protocol"]=="socks"
assert s[0]["settings"]["address"]=="127.0.0.1"
print(s[0]["settings"]["port"])
PY
)
if [[ "$CURRENT" == "$PORT" ]]; then
 echo "ALREADY ACTIVE: universal SOCKS5 switched to dedicated SOCKS5 port $PORT"
 exit 0
fi
[[ "$CURRENT" == 43127 ]] || { echo "ERROR: unexpected current SOCKS5 port $CURRENT"; exit 1; }

BIN=$(sed -n 's/^ExecStart=//p' "$HTTP_UNIT" | head -1 | awk '{print $1}')
[[ -x "$BIN" ]] || { echo "ERROR: existing 3proxy executable not found: $BIN"; exit 1; }
if ! systemctl is-active --quiet "$UNIT"; then
 if ss -H -lnt "( sport = :$PORT )" | grep -q .; then
  echo "ERROR: port $PORT already occupied"; exit 1
 fi
 cat > "$SOCKS_CFG" <<EOF
auth none
socks -p$PORT -i127.0.0.1
EOF
 chown root:vesyamask "$SOCKS_CFG"
 chmod 0640 "$SOCKS_CFG"
 cat > "/etc/systemd/system/$UNIT" <<EOF
[Unit]
Description=Vesya Universal isolated SOCKS5 on loopback
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=vesyamask
Group=vesyamask
ExecStart=$BIN $SOCKS_CFG
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
 systemctl enable --now "$UNIT"
fi
sleep 2
systemctl is-active --quiet "$UNIT" || {
 echo "ERROR: new SOCKS5 service failed, Xray unchanged"
 journalctl -u "$UNIT" -n 20 --no-pager; exit 1
}
ss -lnt | grep -E "127\.0\.0\.1:$PORT[[:space:]]" >/dev/null || {
 echo "ERROR: listener not found on loopback, Xray unchanged"; exit 1
}
echo "Preflight: SOCKS5 with hostname to IP check..."
GOT=$(curl -4 -fsS -m 16 --connect-timeout 7 --socks5-hostname "127.0.0.1:$PORT" https://api.ipify.org || true)
[[ "$GOT" == "$IP" ]] || { echo "ERROR: SOCKS5/ipify got '$GOT', Xray unchanged"; exit 1; }
echo "Preflight: SOCKS5 with hostname to Google..."
CODE=$(curl -4 -LsS -m 20 --connect-timeout 7 -o /dev/null -w '%{http_code}' \
 --socks5-hostname "127.0.0.1:$PORT" https://www.google.com/ || true)
[[ "$CODE" == 200 ]] || { echo "ERROR: SOCKS5/Google HTTP '$CODE', Xray unchanged"; exit 1; }
echo "PASS: two real-domain tests succeeded over isolated SOCKS5"

BACKUP="$CFG.before-socks-3proxy-$(date +%Y%m%d-%H%M%S)"
cp -p "$CFG" "$BACKUP"
CFG="$CFG" PORT="$PORT" python3 - <<'PY'
import json,os,pathlib
p=pathlib.Path(os.environ["CFG"]);d=json.loads(p.read_text())
s=[x for x in d["outbounds"] if x.get("tag")=="personal-socks"]
assert len(s)==1 and s[0]["protocol"]=="socks"
assert s[0]["settings"]["address"]=="127.0.0.1"
assert s[0]["settings"]["port"]==43127
s[0]["settings"]["port"]=int(os.environ["PORT"])
s[0]["targetStrategy"]="UseIPv4"
p.write_text(json.dumps(d,indent=2)+"\n")
PY
rollback() {
 echo "ERROR: rolling back Xray; original services unchanged"
 cp -p "$BACKUP" "$CFG"
 systemctl restart "$XUNIT" || true
}
if ! "$XRAY" run -test -c "$CFG"; then rollback; exit 1; fi
if ! systemctl restart "$XUNIT"; then rollback; exit 1; fi
sleep 2
if ! systemctl is-active --quiet "$XUNIT"; then rollback; exit 1; fi
echo "SUCCESS: universal SOCKS5 uses isolated local 3proxy SOCKS5 on port $PORT"
echo "Legacy microsocks 43127 and personal HTTP 3proxy 43145 unchanged."
echo "Backup: $BACKUP"
echo "Next test from RU: curl -m 20 -x http://127.0.0.1:43141 https://www.google.com/"
