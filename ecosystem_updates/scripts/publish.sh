#!/usr/bin/env bash
# Собирает статический сайт updates.mironbot.ru из notes/*.md
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
python3 "${ROOT}/scripts/publish.py"
echo "OK: ${ROOT}/site/index.html"
