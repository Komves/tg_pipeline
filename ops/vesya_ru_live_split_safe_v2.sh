#!/usr/bin/env bash
# In-place RU split on existing Vesya Xray listener ports 43141/43142.
# Changes ONLY /etc/vesya-universal/ru.json, with verified backup/permissions and rollback.
# Keeps Nginx public 4453/4454, US VPS, all other services and firewall unchanged.
set -Eeuo pipefail
export LC_ALL=C
RU=129.101.115.213
US=186.246.18.103
CONFIG=/etc/vesya-universal/ru.json
SERVICE=vesya-universal-ru.service
XRAY=/opt/vesya-mask/bin/xray
STAGE=''
BACKUP=''
SWITCHED=0
err(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "$EUID" == 0 ]] || err 'root required'
ips=$(ip -4 -o addr show | awk '{split($4,a,"/"); print a[1]}')
grep -Fx "$RU" <<< "$ips" >/dev/null || err "wrong VPS, expected $RU"
[[ -s "$CONFIG" && -x "$XRAY" ]] || err 'RU Xray config or binary missing'
id vesyamask >/dev/null 2>&1 || err 'service user missing'
systemctl is-active --quiet "$SERVICE" || err "original service not active; no changes"
for p in 43141 43142; do
 ss -H -lnt "( sport = :$p )" | grep -q 'LISTEN' || err "local port $p is not listening; no changes"
done
test_config(){
 "$XRAY" run -test -c "$1" >/dev/null 2>&1 &&
 runuser -u vesyamask -- "$XRAY" run -test -c "$1" >/dev/null 2>&1
}
egress(){
 curl --noproxy '' -4fsS --connect-timeout 8 -m 25 \
   -x "http://127.0.0.1:$1" 'https://api.ipify.org/' 2>/dev/null || true
}
page(){
 curl --noproxy '' -4LsS --connect-timeout 8 -m 25 \
   -x "http://127.0.0.1:$1" -o /dev/null -w '%{http_code}' "$2" 2>/dev/null || true
}
direct_page(){
 curl -4LsS --noproxy '*' --connect-timeout 8 -m 25 \
   -o /dev/null -w '%{http_code}' "$1" 2>/dev/null || true
}
ok_code(){ [[ "$1" =~ ^[23][0-9][0-9]$ ]]; }
restore(){
 [[ -s "$BACKUP" ]] || { echo 'CRITICAL: backup missing; do not restart another service' >&2; exit 2; }
 install -o root -g vesyamask -m 0640 "$BACKUP" "$CONFIG"
 if ! test_config "$CONFIG"; then
   echo 'CRITICAL: restored file did not pass Xray validation' >&2; exit 2
 fi
 systemctl reset-failed "$SERVICE" >/dev/null 2>&1 || true
 systemctl restart "$SERVICE" || true
 if systemctl is-active --quiet "$SERVICE" &&
    ss -H -lnt '( sport = :43141 )' | grep -q LISTEN &&
    ss -H -lnt '( sport = :43142 )' | grep -q LISTEN; then
   echo 'ROLLBACK SUCCESS: original route restored, Xray running, ports 43141/43142 listening.'
 else
   echo 'CRITICAL: original config restored but service still needs inspection' >&2
 fi
}
cleanup(){ [[ -z "$STAGE" ]] || rm -f "$STAGE"; }
trap cleanup EXIT

# Repair only permission invariants needed for safe checks; do not alter service.
test_config "$CONFIG" || err 'current config unreadable or invalid for service user; no changes'
if python3 - "$CONFIG" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
sys.exit(0 if any(x.get("tag")=="ru-direct" for x in d.get("outbounds",[])) else 1)
PY
then
 echo 'ALREADY ROUTED: existing RU direct outbound; no changes.'
 exit 0
fi

echo '=== BASELINE: BOTH MODES MUST EXIT THROUGH USA ==='
for p in 43141 43142; do
 ip=$(egress "$p")
 echo "Port $p => ${ip:-NO_RESPONSE}"
 [[ "$ip" == "$US" ]] || err "baseline US egress failed on $p; no changes"
done
echo '=== CHECK NOVOSIBIRSK CAN OPEN RU SITES DIRECTLY ==='
for url in https://mail.ru/ https://timeweb.cloud/; do
 code=$(direct_page "$url")
 echo "DIRECT $url => HTTP $code"
 ok_code "$code" || err "RU direct path fails for $url; no changes"
done

# Save good config first; keep backups privately under /root.
BACKUP="/root/vesya-ru-original-$(date -u +%Y%m%dT%H%M%SZ).json"
cp -p "$CONFIG" "$BACKUP"
chmod 0600 "$BACKUP"
STAGE=$(mktemp /etc/vesya-universal/ru.json.split.XXXXXX)
python3 - "$CONFIG" "$STAGE" <<'PY'
import json,sys,pathlib
d=json.loads(pathlib.Path(sys.argv[1]).read_text())
inbounds={x.get('tag'):x.get('port') for x in d.get('inbounds',[])}
assert inbounds=={'socks-entry':43141,'vesya-entry':43142},f'unexpected inbound layout: {inbounds}'
outs={x.get('tag') for x in d.get('outbounds',[])}
assert {'socks-us','vesya-us'}<=outs and 'ru-direct' not in outs, 'unexpected outbounds'
rules=d.get('routing',{}).get('rules',[])
assert len(rules)==2 and {r.get('outboundTag') for r in rules}=={'socks-us','vesya-us'},'unexpected rules'
assert all(r.get('type')=='field' and set(r.get('inboundTag',[])) in ({'socks-entry'},{'vesya-entry'}) for r in rules),'unexpected inbound routing'
d['outbounds'].append({'tag':'ru-direct','protocol':'freedom','settings':{}})
d['routing']['rules']=[{
  'type':'field',
  'inboundTag':['socks-entry','vesya-entry'],
  'domain':['domain:ru','domain:su','domain:xn--p1ai',
            'domain:timeweb.cloud','domain:timeweb.com',
            'domain:vk.com','domain:ok.ru','domain:2gis.com','domain:yandex.com'],
  'outboundTag':'ru-direct'}]+rules
d['routing']['domainStrategy']='AsIs'
pathlib.Path(sys.argv[2]).write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n')
PY
chown root:vesyamask "$STAGE"
chmod 0640 "$STAGE"
test_config "$STAGE" || err 'new Xray config invalid/unreadable as vesyamask; ORIGINAL UNTOUCHED'

echo '=== SWITCHING LIVE ROUTING (BRIEF XRAY RESTART) ==='
# Atomic swap of tested, correctly owned config; chmod/chown performed BEFORE restart.
cp -p "$STAGE" "$CONFIG.tmp-ru-split"
chown root:vesyamask "$CONFIG.tmp-ru-split"
chmod 0640 "$CONFIG.tmp-ru-split"
mv -f "$CONFIG.tmp-ru-split" "$CONFIG"
SWITCHED=1
if ! systemctl restart "$SERVICE" || ! systemctl is-active --quiet "$SERVICE"; then
 echo 'FAILED: Xray restart; restoring original immediately'
 restore
 exit 1
fi
sleep 2
if ! ( ss -H -lnt '( sport = :43141 )' | grep -q LISTEN &&
       ss -H -lnt '( sport = :43142 )' | grep -q LISTEN ); then
 echo 'FAILED: proxy listener missing after restart; restoring'
 restore
 exit 1
fi

echo '=== LIVE CHECK: RU SITES + US EXIT IN BOTH MODES ==='
for p in 43141 43142; do
  for url in https://mail.ru/ https://timeweb.cloud/; do
    code=$(page "$p" "$url")
    echo "Port $p | $url | HTTP $code"
    if ! ok_code "$code"; then
      echo 'FAILED: Russian website did not load; restoring'
      restore
      exit 1
    fi
  done
  ip=$(egress "$p")
  echo "Port $p | foreign exit => ${ip:-NO_RESPONSE}"
  if [[ "$ip" != "$US" ]]; then
    echo 'FAILED: US egress did not verify; restoring'
    restore
    exit 1
  fi
done
echo 'SUCCESS: LIVE RU domain split active on BOTH original proxy modes.'
echo 'RU .ru/.su/.xn--p1ai and selected RU domains exit directly from Novosibirsk.'
echo 'Other domains use the original US REALITY routes.'
echo "ORIGINAL BACKUP: $BACKUP"
echo 'Nginx ports, US VPS, and firewall were not changed.'
