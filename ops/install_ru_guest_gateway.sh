#!/usr/bin/env bash
# Pilot only: HTTPS proxy entrance in Russia -> authenticated HTTPS proxy in US.
# Target: NEW, clean Ubuntu 24.04 Russian VPS. NEVER run on existing US VPS.
set -Eeuo pipefail

[[ $EUID -eq 0 ]] || { echo "ERROR: run as root on NEW RU VPS"; exit 1; }
[[ "${CONFIRM_NEW_RU_VPS:-}" == "yes" ]] || {
  echo "ERROR: this installer is ONLY for a newly created Russian VPS."
  echo "Set CONFIRM_NEW_RU_VPS=yes to acknowledge. DO NOT run on 186.246.18.103."
  exit 1
}
[[ -n "${ACME_EMAIL:-}" ]] || { echo "ERROR: set ACME_EMAIL to your certificate contact email"; exit 1; }

GATEWAY_HOST="ru.paruslegal.ru"
US_IP="186.246.18.103"
US_PORT="43128"
US_TLS_NAME="paruslegal.ru"

if hostname -I | tr ' ' '\n' | grep -Fxq "$US_IP"; then
  echo "REFUSED: this is the US proxy server."
  exit 1
fi
if [[ -e /etc/nginx/nginx.conf ]]; then
  echo "REFUSED: Nginx configuration already exists. This installer requires a fresh VPS."
  exit 1
fi
for port in 80 443; do
  if ss -lntH "( sport = :$port )" | grep -q .; then
    echo "REFUSED: TCP port $port is already occupied."
    exit 1
  fi
done

if ! getent ahostsv4 "$GATEWAY_HOST" >/dev/null; then
  echo "ERROR: A record for $GATEWAY_HOST is missing. Create DNS A record pointing to this RU VPS."
  exit 1
fi

echo "Installing Nginx stream and Let's Encrypt certificate tools..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y nginx libnginx-mod-stream certbot ca-certificates
systemctl stop nginx

echo "Getting TLS certificate for $GATEWAY_HOST via TCP 80..."
certbot certonly --standalone --non-interactive --agree-tos --email "$ACME_EMAIL" \
  --preferred-challenges http -d "$GATEWAY_HOST"

cat > /etc/nginx/ru-guest-gateway.conf <<EOF
# Dedicated Russian HTTPS entrance; credentials are checked on the US 3proxy.
# Client TLS terminates in Russia. A SECOND independently authenticated TLS
# connection runs from Russia to the existing US TLS proxy.
stream {
    server {
        listen 0.0.0.0:443 ssl;
        ssl_certificate /etc/letsencrypt/live/$GATEWAY_HOST/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/$GATEWAY_HOST/privkey.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_session_cache shared:RU_GATEWAY_SSL:10m;
        ssl_session_timeout 10m;

        proxy_pass $US_IP:$US_PORT;
        proxy_connect_timeout 15s;
        proxy_timeout 30m;
        proxy_ssl on;
        proxy_ssl_server_name on;
        proxy_ssl_name $US_TLS_NAME;
        proxy_ssl_verify on;
        proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;
        proxy_ssl_verify_depth 3;
        proxy_ssl_protocols TLSv1.2 TLSv1.3;
    }
}
EOF

# This installer is intentionally limited to a fresh Ubuntu Nginx installation.
if grep -Eq '^[[:space:]]*stream[[:space:]]*\\{' /etc/nginx/nginx.conf; then
  echo "REFUSED: an existing stream block was found; do not override it."
  exit 1
fi
printf '\ninclude /etc/nginx/ru-guest-gateway.conf;\n' >> /etc/nginx/nginx.conf

install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/ru-gateway-reload.sh <<'EOF'
#!/bin/sh
/usr/sbin/nginx -t && /bin/systemctl reload nginx
EOF
chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/ru-gateway-reload.sh

nginx -t
systemctl enable --now nginx
echo
echo "=== GATEWAY STATUS ==="
systemctl is-active nginx
ss -lnt | grep ':443 ' || true
echo
echo "Ready: https://$GATEWAY_HOST:443 as an HTTPS forward proxy."
echo "Upstream: existing US HTTPS proxy over VERIFIED TLS."
echo "Next: test curl with EXISTING account, and only then distribute RU client."
echo "No services were changed on the US VPS."
