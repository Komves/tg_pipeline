#!/usr/bin/env bash
# Trial guard for Vesya RU gateways. Safe: only NEW TCP SYN packets to proxy ports.
# No SSH/firewall policies/other ports altered. Rules are TEMPORARY until reboot.
set -Eeuo pipefail
EXPECTED_IP=129.101.115.213
CHAIN=VESYA_RU_GUARD
PORTS=4453,4454,43127,4443
[[ "$EUID" -eq 0 ]] || { echo "ERROR: root required"; exit 1; }
ip -4 -o addr show | grep -Fq "$EXPECTED_IP/" || { echo "ERROR: wrong host; expected RU $EXPECTED_IP"; exit 1; }
command -v iptables >/dev/null || { echo "ERROR: iptables unavailable; no changes"; exit 1; }
if systemctl is-active --quiet firewalld.service 2>/dev/null; then
  echo "ERROR: firewalld active. Use its native configuration, no changes."; exit 1
fi
if systemctl is-active --quiet nftables.service 2>/dev/null; then
  echo "ERROR: native nftables service active. Do not mix firewall managers."; exit 1
fi
# Check if exactly our hook is already active. Avoid modifying live rules on re-run.
if iptables -w -C INPUT -p tcp --syn -m multiport --dports "$PORTS" -j "$CHAIN" 2>/dev/null; then
  echo "ALREADY ACTIVE: RU proxy guard"
  iptables -w -nvL "$CHAIN"; exit 0
fi
# Never flush arbitrary INPUT or alter default policy.
if iptables -w -nL "$CHAIN" >/dev/null 2>&1; then
  if iptables -w -S INPUT | grep -Fq -- "-j $CHAIN"; then
    echo "ERROR: stale custom chain referenced by unknown rule. No changes."; exit 1
  fi
  iptables -w -F "$CHAIN"
else
  iptables -w -N "$CHAIN"
fi
rollback() {
  iptables -w -D INPUT -p tcp --syn -m multiport --dports "$PORTS" -j "$CHAIN" 2>/dev/null || true
  iptables -w -F "$CHAIN" 2>/dev/null || true
  iptables -w -X "$CHAIN" 2>/dev/null || true
}
trap 'echo "ERROR: Guard setup failed; removing trial rules"; rollback' ERR
# Limit ONLY new connections: throughput/large file transfers are not throttled.
# 80 simultaneous TCP connections and a generous initial burst of 100 new SYNs per source IP.
iptables -w -A "$CHAIN" -p tcp -m connlimit --connlimit-above 80 --connlimit-mask 32 -j REJECT --reject-with tcp-reset
iptables -w -A "$CHAIN" -m hashlimit --hashlimit-above 60/minute --hashlimit-burst 100 \
  --hashlimit-mode srcip --hashlimit-name vesya_ru_new_syn -j DROP
iptables -w -A "$CHAIN" -j RETURN
iptables -w -I INPUT 1 -p tcp --syn -m multiport --dports "$PORTS" -j "$CHAIN"
trap - ERR
echo "SUCCESS: RU guard active for TCP $PORTS (NEW connections only, per source IP)."
echo "SSH, TLS data throughput, US REALITY and other ports untouched."
echo "TEMPORARY until reboot. Current counters:"
iptables -w -nvL "$CHAIN"
echo "Undo: bash /root/rollback_vesya_ru_guard.sh"
