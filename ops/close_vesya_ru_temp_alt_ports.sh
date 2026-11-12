#!/usr/bin/env bash
# CLOSE ONLY four temporary RU Nginx HTTPS proxy ports from 2026-11-11.
# Keeps 443, 4453, 4454, original guest proxy, US server, and RU guard chain.
set -Eeuo pipefail
IP=129.101.115.213
CONF=/etc/nginx/ru-guest-gateway.conf
CHAIN=VESYA_RU_GUARD
PORTS=8443,2053,2083,2096
fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ "$EUID" -eq 0 ]] || fail "root required"
ips="$(ip -4 -o addr show | awk '{split($4,a,"/");print a[1]}')"
grep -Fx "$IP" <<< "$ips" >/dev/null || fail "wrong VPS: expected Novosibirsk $IP"
[[ -s "$CONF" ]] || fail "Nginx gateway config not found"
systemctl is-active --quiet nginx.service || fail "Nginx not active"
nginx -t >/dev/null || fail "existing nginx config invalid; no changes"
# Validate the precise marked blocks BEFORE touching anything.
CONF="$CONF" python3 - <<'PY'
import os,re,pathlib
s=pathlib.Path(os.environ["CONF"]).read_text()
for p in (8443,2053,2083,2096):
    begin=f"# VESYA_ALT_HTTPS_{p}_BEGIN"
    end=f"# VESYA_ALT_HTTPS_{p}_END"
    if s.count(begin)!=s.count(end) or s.count(begin)>1:
        raise SystemExit(f"ERROR: unexpected marker count on {p}; no changes")
    if begin in s:
        match=re.search(r"(?m)^[ \t]*"+re.escape(begin)+r"[^\n]*\n(?P<block>.*?)^[ \t]*"+re.escape(end)+r"[^\n]*(?:\n|$)",s,re.S)
        if not match:
            raise SystemExit(f"ERROR: port {p} block not found cleanly")
        block=match.group("block")
        if f"listen 0.0.0.0:{p} ssl;" not in block or "proxy_pass 127.0.0.1:43142;" not in block or block.count("server {")!=1 or block.count("}")!=1:
            raise SystemExit(f"ERROR: modified unexpected port {p} block; refusing to remove")
    elif re.search(r"(?m)^\s*listen\s+(?:0\.0\.0\.0:)?"+str(p)+r"\s",s):
        raise SystemExit(f"ERROR: port {p} used outside marked block")
for p in (443,4453,4454):
    if f"listen 0.0.0.0:{p} ssl;" not in s:
        raise SystemExit(f"ERROR: expected gateway port {p} not found")
print("PREFLIGHT OK: only marked temporary listener blocks will be removed")
PY

BACKUP="$CONF.before-close-alt-$(date +%Y%m%d-%H%M%S)"
cp -p "$CONF" "$BACKUP"
CONF="$CONF" python3 - <<'PY'
import os,re,pathlib
p=pathlib.Path(os.environ["CONF"])
s=p.read_text()
for port in (8443,2053,2083,2096):
    begin=f"# VESYA_ALT_HTTPS_{port}_BEGIN"
    end=f"# VESYA_ALT_HTTPS_{port}_END"
    pattern=r"(?m)^[ \t]*"+re.escape(begin)+r"[^\n]*\n.*?^[ \t]*"+re.escape(end)+r"[^\n]*(?:\n|$)"
    s,n=re.subn(pattern,"",s,flags=re.S)
    if n not in (0,1):
        raise SystemExit(f"ERROR: unexpected {port} replacement count {n}")
p.write_text(s)
PY
restore() {
 cp -p "$BACKUP" "$CONF"
 nginx -t >/dev/null 2>&1 && systemctl reload nginx.service || true
}
if ! nginx -t || ! systemctl reload nginx.service; then
 restore
 fail "Nginx validation/reload failed; previous listener config restored"
fi
sleep 1
for p in 8443 2053 2083 2096; do
 if ss -H -lnt "( sport = :$p )" | grep -q 'LISTEN'; then
   restore
   fail "port $p still listening; original Nginx config restored; inspect other services"
 fi
done
# Remove the specific INPUT rate-limit hook for closed ports; other hooks stay untouched.
if iptables -w -nL "$CHAIN" >/dev/null 2>&1; then
  while iptables -w -C INPUT -p tcp --syn -m multiport --dports "$PORTS" -j "$CHAIN" 2>/dev/null; do
    iptables -w -D INPUT -p tcp --syn -m multiport --dports "$PORTS" -j "$CHAIN"
  done
fi
echo "SUCCESS: temporary ports 8443,2053,2083,2096 CLOSED (Nginx listeners removed)."
echo "PRESERVED: 443, 4453, 4454, guest service, US Reality service, remaining guard rules."
echo "BACKUP: $BACKUP"
