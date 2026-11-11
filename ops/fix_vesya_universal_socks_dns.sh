#!/usr/bin/env bash
# Repair only US universal SOCKS5 branch: resolve domains to IPv4 in Xray,
# then pass IP to existing microsocks. Existing 3proxy, RU, and legacy services untouched.
set -Eeuo pipefail
US_IP=186.246.18.103
CONFIG=/etc/vesya-universal/us.json
XRAY=/opt/vesya-mask/bin/xray
UNIT=vesya-universal-us.service
[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root"; exit 1; }
ip -4 -o addr show | grep -Fq "$US_IP/" || { echo "ERROR: wrong VPS; expected $US_IP"; exit 1; }
[[ -f "$CONFIG" && -x "$XRAY" ]] || { echo "ERROR: missing universal Xray files"; exit 1; }
systemctl is-active --quiet "$UNIT" || { echo "ERROR: universal US Xray service not active"; exit 1; }

if python3 - "$CONFIG" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
p=[x for x in d["outbounds"] if x.get("tag")=="personal-socks"]
assert len(p)==1 and p[0].get("protocol")=="socks","Unexpected personal-socks outbound"
sys.exit(0 if p[0].get("targetStrategy")=="UseIPv4" else 1)
PY
then
    echo "ALREADY CONFIGURED: personal-socks targetStrategy=UseIPv4"
    exit 0
fi

BACKUP="$CONFIG.before-socks-dns-$(date +%Y%m%d-%H%M%S)"
cp -p "$CONFIG" "$BACKUP"
export CONFIG
python3 - <<'PY'
import json,os,pathlib
p=pathlib.Path(os.environ["CONFIG"])
d=json.loads(p.read_text())
out=[x for x in d["outbounds"] if x.get("tag")=="personal-socks"]
assert len(out)==1,"Expected exactly one personal-socks outbound"
assert out[0]["protocol"]=="socks","Expected SOCKS protocol"
assert out[0]["settings"]["address"]=="127.0.0.1"
assert out[0]["settings"]["port"]==43127
out[0]["targetStrategy"]="UseIPv4"
p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n")
PY
rollback() {
    echo "ERROR: Restoring previous Xray config"
    cp -p "$BACKUP" "$CONFIG"
    systemctl restart "$UNIT" || true
}
if ! "$XRAY" run -test -c "$CONFIG"; then
    rollback;exit 1
fi
if ! systemctl restart "$UNIT"; then
    rollback;exit 1
fi
sleep 2
if ! systemctl is-active --quiet "$UNIT"; then
    journalctl -u "$UNIT" -n 25 --no-pager || true
    rollback;exit 1
fi
echo "SUCCESS: Xray resolves SOCKS5 targets to IPv4 before microsocks."
echo "Only personal-socks outbound changed; Vesya 3proxy and original SOCKS5 not modified."
echo "Backup: $BACKUP"
echo "Test via RU: curl -4 -m 20 -x http://127.0.0.1:43141 https://api.ipify.org"
