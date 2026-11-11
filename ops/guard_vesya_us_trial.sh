#!/usr/bin/env bash
# Restrict ONLY the new dedicated US REALITY port to RU VPS IPv4.
# No effect on SSH, legacy US SOCKS5 43127, guest service 43128, API 43130.
# TEMPORARY until reboot.
set -Eeuo pipefail
EXPECTED_IP=186.246.18.103
RU_IP=129.101.115.213
CHAIN=VESYA_US_REALITY_GUARD
PORT=24446
[[ "$EUID" -eq 0 ]] || { echo "ERROR: root required";exit 1; }
ip -4 -o addr show | grep -Fq "$EXPECTED_IP/" || { echo "ERROR: wrong host, expected US"; exit 1; }
command -v iptables >/dev/null || { echo "ERROR: iptables unavailable";exit 1; }
if systemctl is-active --quiet firewalld.service 2>/dev/null || systemctl is-active --quiet nftables.service 2>/dev/null; then
 echo "ERROR: another native firewall service active; do not mix managers";exit 1
fi
if ! systemctl is-active --quiet vesya-universal-us.service; then
 echo "ERROR: universal US REALITY not active; refusing protection until operational";exit 1
fi
if iptables -w -C INPUT -p tcp --syn --dport "$PORT" -j "$CHAIN" 2>/dev/null; then
 echo "ALREADY ACTIVE: US REALITY allowlist";iptables -w -nvL "$CHAIN";exit 0
fi
if iptables -w -nL "$CHAIN" >/dev/null 2>&1; then
 if iptables -w -S INPUT | grep -Fq -- "-j $CHAIN"; then
  echo "ERROR: stale chain referenced. No changes.";exit 1
 fi
 iptables -w -F "$CHAIN"
else
 iptables -w -N "$CHAIN"
fi
rollback() {
 iptables -w -D INPUT -p tcp --syn --dport "$PORT" -j "$CHAIN" 2>/dev/null || true
 iptables -w -F "$CHAIN" 2>/dev/null || true
 iptables -w -X "$CHAIN" 2>/dev/null || true
}
trap 'echo "ERROR: US guard setup failed, rolling back";rollback' ERR
# Public endpoint remains bound, but only RU IP can establish NEW sessions.
iptables -w -A "$CHAIN" -s "$RU_IP/32" -j RETURN
iptables -w -A "$CHAIN" -j DROP
iptables -w -I INPUT 1 -p tcp --syn --dport "$PORT" -j "$CHAIN"
trap - ERR
echo "SUCCESS: US REALITY $PORT accepts new connections ONLY from $RU_IP"
echo "TEMPORARY until reboot. Counters:"
iptables -w -nvL "$CHAIN"
echo "Undo: bash /root/rollback_vesya_us_guard.sh"
