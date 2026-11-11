#!/usr/bin/env bash
# Novosibirsk only. Four new HTTPS entrypoints -> EXISTING Vesya 3proxy local 43142.
# No changes to US, 443, 4453, 4454, SSH, existing services.
set -Eeuo pipefail

RU_IP=129.101.115.213
US_IP=186.246.18.103
CONF=/etc/nginx/ru-guest-gateway.conf
CERT=/etc/letsencrypt/live/ru.paruslegal.ru
GUARD=VESYA_RU_GUARD
ALT_PORTS=8443,2053,2083,2096

fail() { echo "ERROR: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail "Run as root on NOVOSIBIRSK server"
LOCAL_IPS=$(ip -4 -o addr show | awk '{split($4,a,"/");print a[1]}')
grep -Fx "$RU_IP" >/dev/null <<< "$LOCAL_IPS" || fail "Wrong host; expected $RU_IP"
[[ -f "$CONF" && -r "$CERT/fullchain.pem" && -r "$CERT/privkey.pem" ]] || fail "Existing RU Nginx gateway/certificate missing"
systemctl is-active --quiet nginx.service || fail "Nginx is not active"
systemctl is-active --quiet vesya-universal-ru.service || fail "RU REALITY service is not active"
command -v iptables >/dev/null || fail "iptables not found"
iptables -w -nL "$GUARD" >/dev/null 2>&1 || fail "Existing rate-limit guard missing; refusing to expose more public proxy ports"
iptables -w -C INPUT -p tcp --syn -m multiport --dports 4453,4454,43127,4443 -j "$GUARD" >/dev/null 2>&1 ||
 fail "Existing RU rate-limit INPUT hook missing; refusing to expose more public proxy ports"

# Verify tested HTTPS :4454 actually forwards to 127.0.0.1:43142.
CONF="$CONF" python3 - <<'PY'
import os,re,pathlib
s=pathlib.Path(os.environ["CONF"]).read_text()
if s.count("stream {")!=1 or not s.rstrip().endswith("}"):
    raise SystemExit("ERROR: unexpected Nginx stream layout")
m=re.search(r"server\s*\{(?:(?!\bserver\s*\{).)*?listen\s+0\.0\.0\.0:4454\s+ssl;(?:(?!\bserver\s*\{).)*?\}",s,re.S)
if not m or not re.search(r"proxy_pass\s+127\.0\.0\.1:43142\s*;",m.group()):
    raise SystemExit("ERROR: 4454 is not the expected working 3proxy gateway")
for port in (8443,2053,2083,2096):
    if re.search(r"listen\s+(?:0\.0\.0\.0:|\[::\]:)?"+str(port)+r"\b",s):
        marker="# VESYA_ALT_HTTPS_"+str(port)+"_BEGIN"
        if marker not in s:
            raise SystemExit("ERROR: port %d already configured by something else" % port)
print("OK: existing 4454 gateway verified")
PY

for port in 8443 2053 2083 2096; do
    marker=$(printf '# VESYA_ALT_HTTPS_%s_BEGIN' "$port")
    if ! grep -Fq "$marker" "$CONF"; then
        if ss -H -lnt "( sport = :$port )" | grep -F "LISTEN" >/dev/null; then
            fail "TCP $port already occupied; no changes"
        fi
    fi
done

BACKUP="$CONF.before-altports-$(date +%Y%m%d-%H%M%S)"
cp -p "$CONF" "$BACKUP"
RULE_ADDED=0
if ! iptables -w -C INPUT -p tcp --syn -m multiport --dports "$ALT_PORTS" -j "$GUARD" 2>/dev/null; then
    iptables -w -I INPUT 1 -p tcp --syn -m multiport --dports "$ALT_PORTS" -j "$GUARD" ||
      fail "Could not enable guard for new ports"
    RULE_ADDED=1
fi

restore() {
    echo "Restoring previous Nginx configuration..."
    cp -p "$BACKUP" "$CONF"
    nginx -t >/dev/null 2>&1 && systemctl reload nginx.service || true
    if [[ "$RULE_ADDED" -eq 1 ]]; then
        iptables -w -D INPUT -p tcp --syn -m multiport --dports "$ALT_PORTS" -j "$GUARD" || true
    fi
}
if ! CONF="$CONF" CERT="$CERT" python3 - <<'PY'
import os,pathlib
p=pathlib.Path(os.environ["CONF"])
s=p.read_text()
for port in (8443,2053,2083,2096):
    begin="# VESYA_ALT_HTTPS_%s_BEGIN" % port
    if begin in s: continue
    block="""
    %s
    server {
        listen 0.0.0.0:%d ssl;
        ssl_certificate %s/fullchain.pem;
        ssl_certificate_key %s/privkey.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
        proxy_pass 127.0.0.1:43142;
        proxy_connect_timeout 15s;
        proxy_timeout 1h;
    }
    # VESYA_ALT_HTTPS_%d_END
"""%(begin,port,os.environ["CERT"],os.environ["CERT"],port)
    i=s.rfind("}")
    s=s[:i]+block+s[i:]
p.write_text(s)
PY
then
    restore
    fail "Could not write alternative HTTPS listeners"
fi
if ! nginx -t; then
    restore
    fail "Nginx configuration invalid; rollback completed"
fi
if ! systemctl reload nginx.service; then
    restore
    fail "Nginx reload failed; rollback completed"
fi

echo "=== NEW LISTENERS ==="
for port in 8443 2053 2083 2096; do
    if ss -H -lnt "( sport = :$port )" | grep -F "LISTEN" >/dev/null; then
        echo "TCP $port : LISTEN"
    else
        echo "TCP $port : NOT LISTENING"
    fi
done
echo "=== END-TO-END CHECK FROM NOVOSIBIRSK ==="
for port in 8443 2053 2083 2096; do
    result=$(curl -4fsS -m 12 --connect-timeout 4 \
      --resolve "ru.paruslegal.ru:$port:127.0.0.1" \
      -x "https://ru.paruslegal.ru:$port" \
      https://api.ipify.org/ 2>/dev/null || true)
    if [[ "$result" == "$US_IP" ]]; then
        echo "HTTPS $port -> US $US_IP : OK"
    else
        echo "HTTPS $port : LISTENER ADDED, end-to-end check inconclusive"
    fi
done
echo "SUCCESS: four extra RU HTTPS proxy ports added under existing rate limits."
echo "Nginx original 443 / 4454 unchanged; US server untouched."
echo "Timeweb cloud firewall may separately need TCP 8443/2053/2083/2096 allowed."
echo "WARNING: Public unauthenticated proxy entries; close unused ports after testing."
echo "Backup: $BACKUP"
