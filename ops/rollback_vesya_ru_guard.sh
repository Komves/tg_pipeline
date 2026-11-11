#!/usr/bin/env bash
set -Eeuo pipefail
CHAIN=VESYA_RU_GUARD
PORTS=4453,4454,43127,4443
[[ "$EUID" -eq 0 ]] || { echo "ERROR: root required"; exit 1; }
if ! iptables -w -nL "$CHAIN" >/dev/null 2>&1; then
 echo "RU guard already absent"; exit 0
fi
iptables -w -D INPUT -p tcp --syn -m multiport --dports "$PORTS" -j "$CHAIN" 2>/dev/null || true
iptables -w -F "$CHAIN"
iptables -w -X "$CHAIN"
echo "SUCCESS: RU trial guard removed. Other firewall rules untouched."
