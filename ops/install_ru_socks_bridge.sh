#!/usr/bin/env bash
# Add one public no-auth SOCKS5 relay on the existing Novosibirsk gateway.
# RUN ONLY ON 129.101.115.213. The US server is NOT modified.
set -Eeuo pipefail

FILE=/etc/nginx/ru-guest-gateway.conf
RU_IP=129.101.115.213
US_IP=186.246.18.103
PORT=43127

if [[ ${EUID} -ne 0 ]]; then echo "ERROR: root required"; exit 1; fi
if ! ip -4 -o addr show | grep -Fq "$RU_IP/"; then
  echo "ERROR: This is not the Novosibirsk server ($RU_IP). No changes."
  exit 1
fi
if [[ ! -f "$FILE" ]] || ! grep -Fq 'proxy_pass 186.246.18.103:43128;' "$FILE"; then
  echo "ERROR: Existing RU HTTPS gateway config not found. No changes."
  exit 1
fi
if grep -Eq "listen[[:space:]]+(0\.0\.0\.0:)?$PORT;" "$FILE"; then
  echo "SOCKS5 relay already configured. Checking:"
  nginx -t
  ss -lnt | grep -E ":$PORT[[:space:]]" || true
  exit 0
fi
if ss -H -lnt "( sport = :$PORT )" | grep -q .; then
  echo "ERROR: Port $PORT already occupied. No changes."
  exit 1
fi
if ! timeout 6 bash -c "exec 9<>/dev/tcp/$US_IP/$PORT" 2>/dev/null; then
  echo "ERROR: Cannot reach US SOCKS5 $US_IP:$PORT from RU server. No changes."
  exit 1
fi

BACKUP="$FILE.bak.$(date +%Y%m%d-%H%M%S)"
cp -p "$FILE" "$BACKUP"
export FILE
python3 - <<'PY'
from pathlib import Path
import os
p = Path(os.environ["FILE"])
s = p.read_text()
if s.count("stream {") != 1 or not s.rstrip().endswith("}"):
    raise SystemExit("Unexpected Nginx config layout; refusing edit")
pos = s.rfind("}")
extra = """
    # Public SOCKS5 TCP relay. NO username/password on either leg.
    # The endpoint is not a VPN, and the TCP segments themselves are not TLS-encrypted.
    server {
        listen 0.0.0.0:43127;
        proxy_pass 186.246.18.103:43127;
        proxy_connect_timeout 10s;
        proxy_timeout 30m;
    }
"""
p.write_text(s[:pos] + extra + s[pos:])
PY

if nginx -t; then
  systemctl reload nginx
  echo "SUCCESS: ru.paruslegal.ru:43127 (SOCKS5) -> $US_IP:$PORT"
  ss -lnt | grep -E ":$PORT[[:space:]]" || true
  echo "Only RU Nginx config changed; backup: $BACKUP"
else
  cp -p "$BACKUP" "$FILE"
  nginx -t || true
  echo "ERROR: Nginx config invalid, old config restored."
  exit 1
fi
