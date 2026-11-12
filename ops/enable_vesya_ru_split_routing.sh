#!/usr/bin/env bash
# Novosibirsk-only safe split routing for the two existing personal HTTPS proxy modes.
# Russia (.ru/.su/.xn--p1ai and selected Russian .com/.cloud domains) exits RU directly;
# all other destinations keep their original US REALITY outbounds.
# Usage: bash enable_vesya_ru_split_routing.sh [apply|rollback|status]
set -Eeuo pipefail
MODE="${1:-apply}"
RU_IP=129.101.115.213
US_IP=186.246.18.103
CONFIG=/etc/vesya-universal/ru.json
BACKUP=/etc/vesya-universal/ru.json.before-ru-split
UNIT=vesya-universal-ru.service
XRAY=/opt/vesya-mask/bin/xray

fail() { echo "ERROR: $*" >&2; exit 1; }
[[ "$EUID" == 0 ]] || fail "root required"
IP_LIST=$(ip -4 -o addr show | awk '{split($4,a,"/");print a[1]}')
grep -Fx "$RU_IP" <<< "$IP_LIST" >/dev/null || fail "wrong server (expected Novosibirsk $RU_IP)"
[[ -s "$CONFIG" && -x "$XRAY" ]] || fail "existing RU proxy config/Xray missing"
systemctl is-active --quiet "$UNIT" || fail "RU universal service is not active; do not change anything"

check_config() {
  "$XRAY" run -test -c "$CONFIG" >/dev/null
}
is_split() {
  python3 - "$CONFIG" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
tags={x.get("tag") for x in d.get("outbounds",[])}
routes=d.get("routing",{}).get("rules",[])
sys.exit(0 if "ru-direct" in tags and any(r.get("outboundTag")=="ru-direct" for r in routes) else 1)
PY
}
egress() {
  local port="$1"
  curl -4 -fsS --max-time 18 --connect-timeout 6 \
    -x "http://127.0.0.1:$port" https://api.ipify.org 2>/dev/null || true
}
page_code() {
  local proxy="$1" url="$2"
  if [[ "$proxy" == direct ]]; then
    curl -4 -LsS --noproxy '*' --connect-timeout 5 --max-time 17 \
      -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || true
  else
    curl -4 -LsS --connect-timeout 5 --max-time 17 \
      -x "http://127.0.0.1:$proxy" -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || true
  fi
}
success_code() {
  [[ "$1" =~ ^[23][0-9][0-9]$ ]]
}
rollback_now() {
  [[ -s "$BACKUP" ]] || fail "backup missing: $BACKUP"
  cp -p "$BACKUP" "$CONFIG"
  if ! check_config || ! systemctl restart "$UNIT"; then
    fail "original configuration restored on disk but restart failed; inspect $UNIT"
  fi
  systemctl is-active --quiet "$UNIT" || fail "original configuration restored but service not running"
  echo "ROLLED BACK: original RU routes restored, proxy services active."
}

case "$MODE" in
  status)
    check_config
    if is_split; then echo "MODE: RU direct for selected Russian domains; other sites US"; else echo "MODE: original (all through US)"; fi
    for p in 43141 43142; do echo "LOCAL $p => $(egress "$p")"; done
    if iptables -w -nL VESYA_RU_GUARD >/dev/null 2>&1; then
      echo "RATE-LIMIT CHAIN: exists"
      iptables -w -S INPUT | grep 'VESYA_RU_GUARD' || echo "RATE-LIMIT HOOK: missing"
    fi
    exit 0
    ;;
  rollback)
    is_split || { echo "ALREADY ORIGINAL: no RU split active"; exit 0; }
    rollback_now
    exit 0
    ;;
  apply) ;;
  *) fail "usage: apply|rollback|status" ;;
esac

if is_split; then
  echo "ALREADY ACTIVE: split routes previously enabled. Checking..."
  "$0" status
  exit 0
fi
[[ ! -e "$BACKUP" ]] || fail "backup already exists but split is not active; inspect $BACKUP (no changes)"
check_config
for p in 43141 43142; do
  got=$(egress "$p")
  [[ "$got" == "$US_IP" ]] || fail "old US path via local $p is not healthy (got '$got'); not changing routing"
done
echo "PREFLIGHT: both old US proxy modes work."

# A direct connection from RU must actually work before enabling Russian routing.
for url in https://mail.ru/ https://timeweb.cloud/; do
  code=$(page_code direct "$url")
  echo "DIRECT RU $url : HTTP $code"
  success_code "$code" || fail "$url not reachable directly from Novosibirsk; leaving original routing intact"
done

# Preserve existing configuration and ownership/mode.
cp -p "$CONFIG" "$BACKUP"
chmod 0600 "$BACKUP"

CONFIG="$CONFIG" python3 - <<'PY'
import json,os,pathlib
path=pathlib.Path(os.environ["CONFIG"])
d=json.loads(path.read_text())
inb={x.get("tag"):x for x in d.get("inbounds",[])}
assert set(inb)=={"socks-entry","vesya-entry"}, "unexpected inbound layout"
assert inb["socks-entry"].get("port")==43141
assert inb["vesya-entry"].get("port")==43142
out={x.get("tag") for x in d.get("outbounds",[])}
assert {"socks-us","vesya-us"}.issubset(out) and "ru-direct" not in out
rules=d.get("routing",{}).get("rules",[])
assert len(rules)==2 and {r.get("outboundTag") for r in rules}=={"socks-us","vesya-us"}, "unexpected existing routing; refusing to modify"
# Route ordinary Russian domains directly out of RU VPS. Include .cloud for Timeweb.
# Not an exhaustive geographic classifier; non-matching sites still use US.
ru_domains=[
 "domain:ru", "domain:su", "domain:xn--p1ai",
 "domain:timeweb.cloud", "domain:timeweb.com",
 "domain:vk.com", "domain:2gis.com", "domain:yandex.com",
]
d["outbounds"].append({"tag":"ru-direct","protocol":"freedom","settings":{}})
rule={"type":"field","inboundTag":["socks-entry","vesya-entry"],
      "domain":ru_domains,"outboundTag":"ru-direct"}
d["routing"]["rules"]=[rule]+rules
# AsIs ensures domains not in this explicit RU list are forwarded to US unchanged.
d["routing"]["domainStrategy"]="AsIs"
path.write_text(json.dumps(d,indent=2,ensure_ascii=False)+"\n")
PY

if ! check_config; then
  echo "Config validation failed; restoring"
  rollback_now
  exit 1
fi
if ! systemctl restart "$UNIT" || ! systemctl is-active --quiet "$UNIT"; then
  echo "Service startup failed; restoring"
  rollback_now
  exit 1
fi
echo "NEW ROUTES: testing both modes..."
for p in 43141 43142; do
  got=$(egress "$p")
  if [[ "$got" != "$US_IP" ]]; then
    echo "Foreign egress verification failed on $p: $got"
    rollback_now
    exit 1
  fi
  for url in https://mail.ru/ https://timeweb.cloud/; do
    code=$(page_code "$p" "$url")
    echo "MODE $p -> $url : HTTP $code"
    if ! success_code "$code"; then
      echo "Russian site check failed; rolling back"
      rollback_now
      exit 1
    fi
  done
done
echo "SUCCESS: both proxy modes now send selected RU sites directly from Novosibirsk, foreign sites via US."
echo "No changes to Nginx ports, guest accounts, US server, or Chrome."
echo "ROLLBACK: bash /root/vesya_ru_split.sh rollback"
echo "NOTE: Geographic RU IP ranges and Russian sites on other TLDs are not covered in this first pass."
