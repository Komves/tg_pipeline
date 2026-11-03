#!/usr/bin/env bash
# Install Vesya's proxy account API alongside the existing guest-proxy services.
# Re-entrant; never touches the personal microsocks service or port 43127.
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then echo "Run as root"; exit 1; fi
for service in guest-proxy guest-proxy-tls guest-proxy-sync.timer; do
  systemctl is-active --quiet "$service" || { echo "Required service not running: $service"; exit 1; }
done
test -f /var/lib/guest-proxy/users.db || { echo "Proxy database missing"; exit 1; }
test -f /etc/stunnel/guest-proxy.conf || { echo "TLS configuration missing"; exit 1; }

id guestproxyapi >/dev/null 2>&1 ||
  useradd --system --user-group --no-create-home --shell /usr/sbin/nologin guestproxyapi

# Only the non-privileged API process may modify the user database.
chown root:guestproxyapi /var/lib/guest-proxy
chmod 0770 /var/lib/guest-proxy
chown root:guestproxyapi /var/lib/guest-proxy/users.db
chmod 0660 /var/lib/guest-proxy/users.db

if [[ ! -s /etc/guest-proxy-api.secret ]]; then
  (umask 077; openssl rand -hex 32 > /etc/guest-proxy-api.secret)
fi
chown root:guestproxyapi /etc/guest-proxy-api.secret
chmod 0640 /etc/guest-proxy-api.secret

# Build a generic Windows binary locally. It contains no passwords;
# personalized settings are added only when a signed-in user opens a link.
CLIENT_DIR=/usr/local/share/vesya-proxy
CLIENT_BIN="$CLIENT_DIR/VesyaProxyClient.exe"
if [[ ! -s "$CLIENT_BIN" ]]; then
  install -d -m 0755 "$CLIENT_DIR" /usr/local/src/vesya-proxy-client
  curl -fsSLo /usr/local/src/vesya-proxy-client/main.go \
    https://raw.githubusercontent.com/Komves/tg_pipeline/master/ops/guest_proxy_client/main.go
  curl -fsSLo /usr/local/src/vesya-proxy-client/go.mod \
    https://raw.githubusercontent.com/Komves/tg_pipeline/master/ops/guest_proxy_client/go.mod
  INSTALLED_GO=0
  if ! command -v go >/dev/null 2>&1; then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y golang-go
    INSTALLED_GO=1
  fi
  (
    cd /usr/local/src/vesya-proxy-client
    CGO_ENABLED=0 GOOS=windows GOARCH=amd64 GOTOOLCHAIN=local GOMAXPROCS=2 \
      go build -trimpath -ldflags="-s -w -H windowsgui" -o "$CLIENT_BIN.tmp" .
  )
  test -s "$CLIENT_BIN.tmp"
  mv "$CLIENT_BIN.tmp" "$CLIENT_BIN"
  chmod 0644 "$CLIENT_BIN"
  if [[ "$INSTALLED_GO" = 1 ]]; then go clean -cache; fi
  echo "Windows client built: $(stat -c %s "$CLIENT_BIN") bytes"
fi

install -d -m 0755 /usr/local/libexec
curl -fsSL https://raw.githubusercontent.com/Komves/tg_pipeline/master/ops/guest_proxy_agent.py \
  -o /usr/local/libexec/guest_proxy_agent.py
python3 -m py_compile /usr/local/libexec/guest_proxy_agent.py
chown root:root /usr/local/libexec/guest_proxy_agent.py
chmod 0644 /usr/local/libexec/guest_proxy_agent.py

cat > /etc/systemd/system/guest-proxy-api.service <<'EOF'
[Unit]
Description=Restricted Vesya guest-proxy account API
After=network.target

[Service]
Type=simple
User=guestproxyapi
Group=guestproxyapi
ExecStart=/usr/bin/python3 -B /usr/local/libexec/guest_proxy_agent.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/guest-proxy
RestrictAddressFamilies=AF_UNIX AF_INET

[Install]
WantedBy=multi-user.target
EOF

# Existing 3proxy/stunnel stays on port 43128; API gets a separate TLS port.
if ! grep -Fqx '[guest-proxy-api]' /etc/stunnel/guest-proxy.conf; then
cat >> /etc/stunnel/guest-proxy.conf <<'EOF'

[guest-proxy-api]
client = no
accept = 0.0.0.0:43130
connect = 127.0.0.1:43131
cert = /etc/letsencrypt/live/paruslegal.ru/fullchain.pem
key = /etc/letsencrypt/live/paruslegal.ru/privkey.pem
EOF
fi

cat > /etc/systemd/system/guest-proxy-sync.path <<'EOF'
[Unit]
Description=Apply guest proxy accounts as soon as database changes

[Path]
PathModified=/var/lib/guest-proxy/users.db
Unit=guest-proxy-sync.service

[Install]
WantedBy=multi-user.target
EOF

# Keep using the renewed Let's Encrypt certificate for the TLS proxy.
install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/restart-guest-proxy-tls.sh <<'EOF'
#!/bin/sh
systemctl try-restart guest-proxy-tls.service
EOF
chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/restart-guest-proxy-tls.sh

systemctl daemon-reload
systemctl enable --now guest-proxy-api.service guest-proxy-sync.path
systemctl restart guest-proxy-tls.service

echo "=== STATUS ==="
for name in guest-proxy guest-proxy-tls guest-proxy-api guest-proxy-sync.timer guest-proxy-sync.path; do
  printf "%s: " "$name"; systemctl is-active "$name"
done
echo "=== NEW PORTS ==="
ss -lntp | grep -E ':43128|:43129|:43130|:43131' || true
echo "=== API CHECK ==="
TOKEN="$(cat /etc/guest-proxy-api.secret)"
curl -fsS --noproxy '*' --max-time 10 \
  --resolve paruslegal.ru:43130:127.0.0.1 \
  -H "X-Proxy-Token: ${TOKEN}" -H "Content-Type: application/json" \
  -d '{"telegram_id":1}' "https://paruslegal.ru:43130/v1/status"
unset TOKEN
echo
echo "Done. Never publish the API token. Add it to Render as GUEST_PROXY_API_TOKEN."