#!/usr/bin/env bash
# Hardening for RU universal HTTPS forward-proxy, preserving existing working route.
# Usage: bash harden_vesya_universal_ru.sh prepare|activate|rollback
# prepare: stage separate authenticated HTTP inbounds and validate them, NO public changes.
# activate: switch only existing RU HTTPS ports 4453/4454 to authentication + per-IP conn limit.
# rollback: restore previously backed-up Nginx stream config, keeping original services.
set -Eeuo pipefail

MODE="$1"
RU_IP=129.101.115.213
US_IP=186.246.18.103
ROOT=/etc/vesya-universal
PEER=/root/vesya-universal-peer.json
SECRETS=/root/vesya-universal-auth.json
CONFIG=$ROOT/secure-ru.json
XRAY=/opt/vesya-mask/bin/xray
UNIT=vesya-universal-secure-ru.service
NGINX=/etc/nginx/ru-guest-gateway.conf
BACKUP=/etc/nginx/ru-guest-gateway.conf.before-vesya-auth

die() { echo "ERROR: $*" >&2;exit 1; }
[[ $EUID -eq 0 ]] || die "Run as root"
ip -4 -o addr show | grep -Fq "$RU_IP/" || die "This is not the Novosibirsk VPS $RU_IP"
[[ -f "$NGINX" && -s "$PEER" && -x "$XRAY" ]] || die "Existing RU gateway, peer credentials or Xray missing"

if [[ "$MODE" == "rollback" ]]; then
 [[ -f "$BACKUP" ]] || die "No saved original Nginx config"
 cp -p "$BACKUP" "$NGINX"
 nginx -t && systemctl reload nginx || die "Restored original Nginx file but reload failed; inspect manually"
 echo "ROLLBACK OK: both 4453/4454 use their original unauthenticated backends"
 exit 0
fi

[[ "$MODE" == "prepare" || "$MODE" == "activate" ]] || die "Use prepare, activate or rollback"

if [[ "$MODE" == "prepare" ]]; then
 # Keep the generated key stable across reruns. Never print it in automated logs.
 if [[ ! -f "$SECRETS" ]]; then
  umask 077
  python3 - "$SECRETS" <<'PY'
import json,secrets,sys,pathlib
p=pathlib.Path(sys.argv[1])
with p.open("x") as f:
 json.dump({"user":"personal","pass":secrets.token_urlsafe(30)},f)
 f.write("\n")
p.chmod(0o600)
PY
 fi
 for port in 43151 43152; do
  if [[ ! -e "$CONFIG" ]] && ss -H -lnt "( sport = :$port )" | grep -q .; then
   die "Local port $port already occupied"
  fi
 done
 [[ -f "$ROOT/ru.json" ]] || die "Original RU Xray config missing"
 install -d -m 0750 -o root -g vesyamask "$ROOT"
 PEER="$PEER" SECRET="$SECRETS" OUT="$CONFIG" US="$US_IP" python3 - <<'PY'
import json,os,pathlib
old=json.loads(pathlib.Path("/etc/vesya-universal/ru.json").read_text())
creds=json.loads(pathlib.Path(os.environ["SECRET"]).read_text())
assert creds["user"]=="personal" and len(creds["pass"])>=32
p=json.loads(pathlib.Path(os.environ["PEER"]).read_text())
assert p["host"]==os.environ["US"] and p["port"]==24446
# Reuse exact tested REALITY outbound settings and routing from current working config.
outbounds=old["outbounds"]
tags={x["tag"] for x in outbounds}
assert {"socks-us","vesya-us"}.issubset(tags)
inbounds=[
 {"tag":"socks-auth","listen":"127.0.0.1","port":43151,"protocol":"http",
  "settings":{"users":[creds],"allowTransparent":False}},
 {"tag":"vesya-auth","listen":"127.0.0.1","port":43152,"protocol":"http",
  "settings":{"users":[creds],"allowTransparent":False}}
]
rules=[{"type":"field","inboundTag":[mode+"-auth"],"outboundTag":mode+"-us"}
       for mode in ("socks","vesya")]
conf={"log":{"loglevel":"warning"},"inbounds":inbounds,"outbounds":outbounds,
      "routing":{"rules":rules}}
dest=pathlib.Path(os.environ["OUT"])
dest.write_text(json.dumps(conf,indent=2)+"\n")
dest.chmod(0o640)
PY
 chown root:vesyamask "$CONFIG"
 "$XRAY" run -test -c "$CONFIG" || die "New Xray config invalid; existing services not changed"
 cat > "/etc/systemd/system/$UNIT" <<EOF
[Unit]
Description=Vesya authenticated personal HTTP proxy inbounds (parallel to old)
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
 systemctl restart "$UNIT"
 sleep 2
 systemctl is-active --quiet "$UNIT" || {
  journalctl -u "$UNIT" -n 30 --no-pager; die "Protected backend did not start"; }
fi

[[ -f "$SECRETS" && -f "$CONFIG" ]] || die "Run prepare first"
systemctl is-active --quiet "$UNIT" || die "Protected backend service is not active"
user=$(python3 -c 'import json;print(json.load(open("/root/vesya-universal-auth.json"))["user"])')
pass=$(python3 -c 'import json;print(json.load(open("/root/vesya-universal-auth.json"))["pass"])')

# Both backends must require credentials and provide the verified US exit when given them.
for port in 43151 43152; do
 if curl -sSf -m 12 -x "http://127.0.0.1:$port" "https://api.ipify.org/" >/dev/null 2>&1; then
  die "FAIL SAFE: local port $port DOES NOT require authentication; existing public config unchanged"
 fi
 ip=$(curl -4fsS -m 35 --proxy-user "$user:$pass" \
  -x "http://127.0.0.1:$port" https://api.ipify.org/ || true)
 [[ "$ip" == "$US_IP" ]] || die "FAIL SAFE: auth backend $port egress was '$ip'; public ports unchanged"
 echo "PASS: protected local $port requires authentication and exits through $US_IP"
done

if [[ "$MODE" == "prepare" ]]; then
 echo "PREPARE SUCCESS: existing public ports 4453/4454 are STILL UNCHANGED."
 echo "Secret for Chrome extension: $SECRETS (do not post it online)."
 echo "Install the updated Chrome extension, save the secret, THEN run activate."
 exit 0
fi

# Only switch public front after new version of extension is installed with secret.
if grep -Fq 'proxy_pass 127.0.0.1:43151;' "$NGINX" &&
   grep -Fq 'proxy_pass 127.0.0.1:43152;' "$NGINX"; then
 echo "ALREADY ACTIVE: public 4453/4454 point to protected backends."
 exit 0
fi
for oldport in 43141 43142; do
 [[ $(grep -Fxc "        proxy_pass 127.0.0.1:$oldport;" "$NGINX") -eq 1 ]] ||
  die "Unexpected Nginx config for old local port $oldport; refuse edits"
done
[[ ! -e "$BACKUP" ]] || die "Backup already exists but active config differs; inspect before retry"
cp -p "$NGINX" "$BACKUP"
NGINX="$NGINX" python3 - <<'PY'
import os,pathlib,re
p=pathlib.Path(os.environ["NGINX"])
s=p.read_text()
assert s.count("stream {")==1
for old,new in ((43141,43151),(43142,43152)):
 pattern="proxy_pass 127.0.0.1:%d;"%old
 assert s.count(pattern)==1,(pattern,s.count(pattern))
 s=s.replace(pattern,"proxy_pass 127.0.0.1:%d;"%new,1)
# Bound connection count, not transfer speed, for the two personal HTTPS listeners.
if "vesya_secure_perip" not in s:
 s=s.replace("stream {","stream {\n    limit_conn_zone $binary_remote_addr zone=vesya_secure_perip:1m;",1)
for port in (4453,4454):
 text="listen 0.0.0.0:%d ssl;"%port
 assert s.count(text)==1,(text,s.count(text))
 if "limit_conn vesya_secure_perip" not in s[s.index(text):s.index(text)+160]:
  s=s.replace(text,text+"\n        limit_conn vesya_secure_perip 40;",1)
p.write_text(s)
PY
if ! nginx -t || ! systemctl reload nginx; then
 cp -p "$BACKUP" "$NGINX"
 nginx -t && systemctl reload nginx || true
 die "Nginx rejected change. Rolled back to working public ports."
fi
# External check; rollback automatically if either route does not work.
for port in 4453 4454; do
 if curl -sSf -m 15 -x "https://ru.paruslegal.ru:$port" "https://api.ipify.org/" >/dev/null 2>&1; then
  cp -p "$BACKUP" "$NGINX";nginx -t && systemctl reload nginx || true
  die "Proxy $port still allows no-password access: rolled back"
 fi
 ip=$(curl -4fsS -m 35 -x "https://ru.paruslegal.ru:$port" \
   --proxy-user "$user:$pass" https://api.ipify.org/ || true)
 if [[ "$ip" != "$US_IP" ]]; then
  cp -p "$BACKUP" "$NGINX";nginx -t && systemctl reload nginx || true
  die "External proxy $port auth test failed: rolled back"
 fi
 echo "PASS: HTTPS $port requires personal access key and exits through US"
done
echo "ACTIVATION SUCCESS: 4453/4454 protected by proxy authentication + 40 per-IP connections."
echo "Legacy unauthenticated RU:43127 (if installed) requires separate closure after migration."
echo "Quick rollback (restores old unprotected listeners): bash $0 rollback"
