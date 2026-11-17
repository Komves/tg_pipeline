#!/usr/bin/env bash
# Read-only inventory before deploying the dedicated GPT management API.
# No credentials, passwords, private keys, or contents of auth files printed.
set -u
printf '\n=== SERVER ===\n'
hostname -f 2>/dev/null || hostname
uname -sr
printf '\n=== SYSTEMD / TOOLS ===\n'
systemd --version | head -n1 || true
for name in python3 htpasswd nginx openssl; do command -v "$name" || true; done
printf '\n=== EXISTING SERVICES (NO RESTART) ===\n'
for unit in nginx squid squid-gpt; do
  printf '%s: ' "$unit"
  systemctl is-active "$unit" 2>/dev/null || true
done
printf '\n=== LISTENERS (NO SECRETS) ===\n'
ss -ltnp 2>/dev/null | grep -E ':(443|4453|4454|4455|3131|3132|3133|43141)\b' || true
printf '\n=== PERMISSIONS (NO FILE CONTENT) ===\n'
for p in /etc/squid-gpt /etc/squid-gpt/users /var/lib/squid-gpt-agent /var/lib/squid-gpt-sync /etc/squid-gpt-agent/token; do
  if [ -e "$p" ]; then
    stat -c '%U:%G %a %F %n' "$p"
  else
    echo "absent: $p"
  fi
done
printf '\n=== USERS / GROUPS (NO PASSWORDS) ===\n'
getent passwd proxy 2>/dev/null | cut -d: -f1,3,4 || true
getent group proxy 2>/dev/null | cut -d: -f1,3 || true
id gpt-agent 2>/dev/null || true
printf '\n=== NGINX CONFIG VALIDATION (NO RELOAD) ===\n'
nginx -t 2>&1 | tail -n3 || true
printf '\n=== END OF READ-ONLY PREFLIGHT ===\n'
