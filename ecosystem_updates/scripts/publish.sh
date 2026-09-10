#!/usr/bin/env bash
# Собирает статический сайт updates.mironbot.ru из notes/*.md
# Вызывать только из успешного deploy_prod.sh (не вручную после кодинга на dev).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Черновики из inbox/ → notes/ на дату МСК (один раз при деплое).
TODAY_MSK="$(TZ=Europe/Moscow date +%F)"
INBOX="${ROOT}/inbox/${TODAY_MSK}.md"
NOTE="${ROOT}/notes/${TODAY_MSK}.md"
if [[ -f "${INBOX}" ]]; then
  mkdir -p "${ROOT}/notes"
  mv -f "${INBOX}" "${NOTE}"
  echo "==> inbox → notes: ${TODAY_MSK}.md"
fi

python3 "${ROOT}/scripts/publish.py"
echo "OK: ${ROOT}/site/index.html"
