#!/usr/bin/env bash
set -Eeuo pipefail
CHAIN=VESYA_US_REALITY_GUARD
PORT=24446
[[ "$EUID" -eq 0 ]] || { echo "ERROR: root required";exit 1; }
if ! iptables -w -nL "$CHAIN" >/dev/null 2>&1; then
 echo "US guard already absent";exit 0
fi
iptables -w -D INPUT -p tcp --syn --dport "$PORT" -j "$CHAIN" 2>/dev/null || true
iptables -w -F "$CHAIN"
iptables -w -X "$CHAIN"
echo "SUCCESS: US REALITY guard removed, other firewall rules untouched."
