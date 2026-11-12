#!/usr/bin/env bash
# Vesya Novosibirsk Xray RU split routing, v3
# Preserve live HTTPS 4453/4454 and US REALITY. Add geosite category-ru + geoip:ru
# to both old local HTTP proxy modes with automatic rollback on failed checks.
set -Eeuo pipefail
export LC_ALL=C
RU=129.101.115.213
US=186.246.18.103
CONF=/etc/vesya-universal/ru.json
XRAY=/opt/vesya-mask/bin/xray
UNIT=vesya-universal-ru.service
ASSETS=/usr/local/share/xray
TMP=''
NEW=''
BACKUP=''
die(){ echo "ERROR: $*" >&2; exit 1; }
[[ "$EUID" -eq 0 ]] || die "root required"
ips=$(ip -4 -o addr show | awk '{split($4,a,"/");print a[1]}')
grep -Fx "$RU" <<< "$ips" >/dev/null || die "wrong VPS; expected $RU"
[[ -s "$CONF" && -x "$XRAY" ]] || die "existing Xray binary/config missing"
id vesyamask >/dev/null 2>&1 || die "vesyamask service user missing"
systemctl is-active --quiet "$UNIT" || die "original RU service must be running"
systemctl is-active --quiet nginx || die "Nginx must be running"
for port in 43141 43142; do
  ss -H -lnt "( sport = :$port )" | grep -F LISTEN >/dev/null || die "original local $port not listening"
done
cleanup(){ [[ -z "$TMP" ]] || rm -f "$TMP"; [[ -z "$NEW" ]] || rm -f "$NEW"; }
trap cleanup EXIT

# Confirm current service environment doesn't specify an unrelated geodata location.
asset_override=$(systemctl show "$UNIT" -p Environment --value | tr ' ' '\n' | sed -n 's/^XRAY_LOCATION_ASSET=//p' | head -1)
if [[ -n "$asset_override" && "$asset_override" != "$ASSETS" ]]; then
  die "custom XRAY_LOCATION_ASSET=$asset_override; no changes"
fi
test_config(){
  "$XRAY" run -test -c "$1" >/dev/null &&
  runuser -u vesyamask -- "$XRAY" run -test -c "$1" >/dev/null
}
proxy_ip(){
  curl -4 -fsS --noproxy '' --connect-timeout 8 --max-time 27 \
     -x "http://127.0.0.1:$1" https://api.ipify.org/ 2>/dev/null || true
}
proxy_http(){
  curl -4 -LsS --noproxy '' --connect-timeout 8 --max-time 25 \
     -x "http://127.0.0.1:$1" -o /dev/null -w '%{http_code}' "$2" 2>/dev/null || true
}
direct_http(){
  curl -4 -LsS --noproxy '*' --connect-timeout 8 --max-time 25 \
     -o /dev/null -w '%{http_code}' "$1" 2>/dev/null || true
}
http_ok(){ [[ "$1" =~ ^[23][0-9][0-9]$ ]]; }
restore(){
  echo 'RESTORING original Xray config with correct file permissions...'
  [[ -s "$BACKUP" ]] || die 'NO BACKUP, manual restore required'
  install -o root -g vesyamask -m 0640 "$BACKUP" "$CONF"
  "$XRAY" run -test -c "$CONF" >/dev/null || die "restored config invalid"
  systemctl reset-failed "$UNIT" >/dev/null 2>&1 || true
  systemctl restart "$UNIT" || die "restored config but service restart failed"
  sleep 2
  systemctl is-active --quiet "$UNIT" || die "restored config but service inactive"
  echo 'ROLLBACK OK: original configuration and permissions restored, Xray running.'
}

test_config "$CONF" || die "working config unreadable by vesyamask; no changes"
if python3 - "$CONF" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
sys.exit(0 if any(x.get('tag')=='ru-direct' for x in d.get('outbounds',[])) else 1)
PY
then
  echo 'ALREADY CONFIGURED: ru-direct exists; not changing an unknown active config.'
  exit 0
fi

echo '=== VERIFY CURRENT PROXIES ==='
for port in 43141 43142; do
  ip=$(proxy_ip "$port")
  echo "$port egress: ${ip:-NO_REPLY}"
  [[ "$ip" == "$US" ]] || die "mode $port US exit unavailable; nothing changed"
done
echo '=== VERIFY DIRECT RU SITES FROM NOVOSIBIRSK ==='
for site in https://mail.ru/ https://timeweb.cloud/; do
  code=$(direct_http "$site")
  echo "$site direct: HTTP $code"
  http_ok "$code" || die "direct RU site cannot be reached; nothing changed"
done

echo '=== PREPARE OFFICIAL GEODATA ==='
install -d -m 0755 "$ASSETS"
for item in 'geoip.dat:geoip-lite.dat' 'geosite.dat:geosite.dat'; do
  dst="${item%%:*}"
  src="${item#*:}"
  if [[ -s "$ASSETS/$dst" ]]; then
    echo "$dst already exists; keeping original file"
  else
    TMP=$(mktemp "$ASSETS/$dst.download.XXXXXX")
    curl -fL -sS --retry 2 --connect-timeout 15 --max-time 140 \
      "https://github.com/XTLS/Xray-rules-dat/releases/latest/download/$src" -o "$TMP" ||
      die "cannot download official $src; working services unchanged"
    bytes=$(stat -c %s "$TMP")
    [[ "$bytes" -ge 100000 ]] || die "$src too small; working services unchanged"
    install -m 0644 "$TMP" "$ASSETS/$dst"
    rm -f "$TMP"; TMP=''
    echo "Installed geodata $dst ($bytes bytes)"
  fi
done

# Only stage config with .json extension; Xray must be able to detect its format.
BACKUP="/root/vesya-ru-before-geo-$(date -u +%Y%m%dT%H%M%SZ).json"
cp -p "$CONF" "$BACKUP"
chmod 0600 "$BACKUP"
NEW=$(mktemp --suffix=.json /etc/vesya-universal/ru.geo.XXXXXX)
python3 - "$CONF" "$NEW" <<'PY'
import json,pathlib,sys
existing=pathlib.Path(sys.argv[1])
data=json.loads(existing.read_text())
ins={x.get('tag'):x.get('port') for x in data.get('inbounds',[])}
assert ins=={'socks-entry':43141,'vesya-entry':43142},f'unexpected inbounds {ins}'
outs={x.get('tag') for x in data.get('outbounds',[])}
assert {'socks-us','vesya-us'} <= outs and 'ru-direct' not in outs,'unexpected outbounds'
rules=data.get('routing',{}).get('rules',[])
assert len(rules)==2,'unexpected number of old routes'
assert {r.get('outboundTag') for r in rules}=={'socks-us','vesya-us'},'unexpected original outbounds'
assert all(r.get('type')=='field' and len(r.get('inboundTag',[]))==1 for r in rules),'unexpected original route types'
data['outbounds'].append({'tag':'ru-direct','protocol':'freedom','settings':{}})
doms=['domain:ru','domain:su','domain:xn--p1ai',
      'geosite:category-ru',
      'domain:timeweb.cloud','domain:timeweb.com',
      'domain:vk.com','domain:ok.ru','domain:2gis.com','domain:yandex.com',
      'domain:ozon.com','domain:sberbank.com','domain:rzd.com']
data['routing']['rules']=[
  {'type':'field','inboundTag':['socks-entry','vesya-entry'],
   'domain':doms,'outboundTag':'ru-direct'},
  {'type':'field','inboundTag':['socks-entry','vesya-entry'],
   'ip':['geoip:ru'],'outboundTag':'ru-direct'},
]+rules
# Crucial: both inbounds have an explicit existing US fallback rule. IPIfNonMatch
# would never match IPs of domain destinations before this fallback. IPOnDemand
# resolves destination IP at the geoip rule, preserving per-inbound US outbounds.
data['routing']['domainStrategy']='IPOnDemand'
pathlib.Path(sys.argv[2]).write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
PY
chown root:vesyamask "$NEW"
chmod 0640 "$NEW"
echo '=== VALIDATE NEW CONFIGURATION AS SERVICE USER ==='
if ! test_config "$NEW"; then
  echo 'PROPOSED ROUTES FAILED TEST: NO LIVE CHANGES'
  exit 1
fi
echo '=== REPLACE ONE CONFIG AND RESTART RU XRAY ==='
# Move the fully checked readable file atomically into place.
mv -f "$NEW" "$CONF"
NEW=''
if ! systemctl restart "$UNIT" || ! systemctl is-active --quiet "$UNIT"; then
  echo 'FAILED TO START: automatic rollback'
  restore
  exit 1
fi
sleep 2
for port in 43141 43142; do
  if ! ss -H -lnt "( sport = :$port )" | grep -F LISTEN >/dev/null; then
    echo "LOCAL PORT $port MISSING: automatic rollback"
    restore
    exit 1
  fi
done

echo '=== REAL ROUTE TESTS THROUGH BOTH MODES ==='
for port in 43141 43142; do
  for site in https://mail.ru/ https://timeweb.cloud/; do
    code=$(proxy_http "$port" "$site")
    echo "$port $site : HTTP $code"
    if ! http_ok "$code"; then
      echo 'FAILED RUSSIAN SITE: automatic rollback'
      restore
      exit 1
    fi
  done
  ip=$(proxy_ip "$port")
  echo "$port non-RU api.ipify.org exit: ${ip:-NO_REPLY}"
  if [[ "$ip" != "$US" ]]; then
    echo 'FAILED US EXIT: automatic rollback'
    restore
    exit 1
  fi
done
echo 'SUCCESS: 4453/4454 kept. RU domains (geosite+TLD) and RU IPs (geoip) go DIRECT from Novosibirsk; others via US.'
echo 'NOTE: Geo data is an approximation. Some global/CDN or unusual Russian-hosted sites can still use US.'
echo "RESTORE FILE: $BACKUP"
