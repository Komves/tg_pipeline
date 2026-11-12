#!/usr/bin/env bash
# One-step RU maintenance: remove temporary November 11 HTTPS listeners,
# then enable previously tested safe direct-RU / US split routing.
set -Eeuo pipefail
[[ "$EUID" == 0 ]] || { echo "ERROR: use root shell"; exit 1; }
ips="$(ip -4 -o addr show | awk '{split($4,a,"/");print a[1]}')"
grep -Fx '129.101.115.213' <<< "$ips" >/dev/null || { echo "ERROR: run on Novosibirsk VPS only"; exit 1; }

BASE=https://raw.githubusercontent.com/Komves/tg_pipeline/master/ops
CLEANUP=/root/close_vesya_ru_temp_alt_ports.sh
ROUTE=/root/vesya_ru_split.sh
curl -fsSLo "$CLEANUP" "$BASE/close_vesya_ru_temp_alt_ports.sh"
curl -fsSLo "$ROUTE" "$BASE/enable_vesya_ru_split_routing.sh"
echo "=== STEP 1: CLOSE TEMPORARY PORTS ==="
bash "$CLEANUP"
echo "=== STEP 2: ROUTE RUSSIAN SITES THROUGH RU, ALL OTHERS THROUGH US ==="
if bash "$ROUTE" apply; then
  echo "=== FINISHED: TEMP PORTS CLOSED, RU SPLIT ACTIVE ==="
else
  echo "=== PARTIAL: TEMP PORTS CLOSED, ROUTING CHANGE NOT APPLIED OR ROLLED BACK ===" >&2
  exit 1
fi
