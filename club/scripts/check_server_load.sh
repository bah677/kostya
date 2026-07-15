#!/usr/bin/env bash
# Быстрая проверка перед деплоем или тяжёлой работой агента.
set -euo pipefail

echo "=== uptime / load ==="
uptime

echo
echo "=== memory ==="
free -h

echo
echo "=== disk / ==="
df -h / | tail -1

echo
echo "=== python bots (count / RSS MB) ==="
mapfile -t bots < <(pgrep -af 'python3.*main.py' 2>/dev/null | grep -v check_server || true)
echo "count: ${#bots[@]}"
ps -C python3 -o rss= 2>/dev/null | awk '{s+=$1} END {printf "python RSS total: %.0f MB\n", s/1024}'

echo
echo "=== cursor/node RSS MB ==="
ps -C node -o rss= 2>/dev/null | awk '{s+=$1} END {printf "node RSS total: %.0f MB\n", s/1024}'

avail_kb=$(grep MemAvailable /proc/meminfo | awk '{print $2}')
load1=$(awk '{print $1}' /proc/loadavg)
warn=0
if [[ "${avail_kb}" -lt 1048576 ]]; then
  echo "WARN: MemAvailable < 1 GB — не деплоить и не гонять тяжёлые команды"
  warn=1
fi
if awk "BEGIN {exit !(${load1} > 4.0)}"; then
  echo "WARN: load1=${load1} > 4 — подождите"
  warn=1
fi
exit "${warn}"
