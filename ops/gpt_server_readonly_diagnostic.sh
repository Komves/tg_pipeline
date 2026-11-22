#!/usr/bin/env bash
# One-time READ-ONLY production diagnosis of the existing dedicated GPT proxy.
# No service reload/restart, no credentials, no user hashes, no config changes.
set -uo pipefail

echo '=== GPT proxy server: read-only status ==='
date -u '+UTC %Y-%m-%dT%H:%M:%SZ'
hostname
for unit in nginx squidgpt squid-gpt gpt-agent; do
  printf '%-15s ' "$unit"
  systemctl is-active "$unit" 2>/dev/null || true
done

echo '=== Bound TCP ports (GPT and adjacent routes only) ==='
ss -ltnp 2>/dev/null | grep -E ':(4454|4455|3133|3132|43143|4460)([[:space:]]|$)' || true

echo '=== nginx configuration check, no reload ==='
nginx -t 2>&1 | tail -n4 || true

echo '=== nginx selected routing lines (no certificates, no secrets) ==='
nginx -T 2>/dev/null | grep -nE 'listen[[:space:]][^;]*(4454|4455)|proxy_pass[[:space:]]+127[.]0[.]0[.]1:(3132|3133)|ssl_verify_client|ssl_reject_handshake' | head -n30 || true

echo '=== unauthenticated local GPT Squid CONNECT (expected HTTP 407) ==='
python3 - <<'PY'
import socket
for port in (3133,3132):
    try:
        with socket.create_connection(("127.0.0.1",port),timeout=3) as s:
            s.settimeout(5)
            s.sendall(b"CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\r\nProxy-Connection: close\r\n\r\n")
            data=s.recv(300)
            print(f"127.0.0.1:{port}:",data.split(b"\r\n",1)[0].decode("ascii","replace") if data else "EMPTY HTTP RESPONSE")
    except OSError as exc:
        print(f"127.0.0.1:{port}: {type(exc).__name__}: {exc}")
PY

echo '=== systemd recent nginx and Squid failures, no payloads ==='
for unit in nginx squidgpt squid-gpt; do
  printf '%s failures: ' "$unit"
  systemctl --no-pager --property=NRestarts,ExecMainStatus show "$unit" 2>/dev/null | tr '\n' ';' || true
  echo
done
echo '=== End of GPT read-only diagnosis ==='
