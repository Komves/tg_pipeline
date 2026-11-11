#!/usr/bin/env bash
# Restore the exact trial rate limits after the temporary no-guard test.
# No changes to Nginx, Xray, US server, SSH, or the 443 guest service.
set -Eeuo pipefail
CHAIN=VESYA_RU_GUARD
MAIN=4453,4454,43127,4443
ALT=8443,2053,2083,2096
[[ "${EUID}" -eq 0 ]] || { echo "ERROR: must run as root"; exit 1; }
ip -4 -o addr show | grep -Fq '129.101.115.213/' || { echo "ERROR: wrong VPS"; exit 1; }
command -v iptables >/dev/null || { echo "ERROR: iptables missing"; exit 1; }
if ! iptables -w -nL "$CHAIN" >/dev/null 2>&1; then
  iptables -w -N "$CHAIN"
fi
rules="$(iptables -w -S "$CHAIN")"
if ! grep -q '^-A ' <<< "$rules"; then
  iptables -w -A "$CHAIN" -p tcp -m connlimit --connlimit-above 80 --connlimit-mask 32 -j REJECT --reject-with tcp-reset
  iptables -w -A "$CHAIN" -m hashlimit --hashlimit-above 60/minute --hashlimit-burst 100 --hashlimit-mode srcip --hashlimit-name vesya_ru_new_syn -j DROP
  iptables -w -A "$CHAIN" -j RETURN
else
  grep -q -- '--connlimit-above 80' <<< "$rules" || { echo "ERROR: unexpected pre-existing chain rules"; exit 1; }
  grep -q -- 'hashlimit' <<< "$rules" || { echo "ERROR: incomplete chain"; exit 1; }
fi
for ports in "$MAIN" "$ALT"; do
  if ! iptables -w -C INPUT -p tcp --syn -m multiport --dports "$ports" -j "$CHAIN" 2>/dev/null; then
    iptables -w -I INPUT 1 -p tcp --syn -m multiport --dports "$ports" -j "$CHAIN"
  fi
done
echo "RESTORED: RU guard rate limits for original and alternative proxy ports."
iptables -w -nvL "$CHAIN" | head -n 8
