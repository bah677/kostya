#!/usr/bin/env bash
# Собирает статический сайт updates.mironbot.ru из notes/*.md
# Вызывать только из успешного deploy_prod.sh (не вручную после кодинга на dev).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Черновики из inbox/ → notes/ на дату МСК (один раз при деплое).
TODAY_MSK="$(TZ=Europe/Moscow date +%F)"

promote_inbox() {
  local inbox_dir="$1"
  local notes_dir="$2"
  local inbox="${inbox_dir}/${TODAY_MSK}.md"
  local note="${notes_dir}/${TODAY_MSK}.md"
  if [[ -f "${inbox}" ]]; then
    mkdir -p "${notes_dir}"
    mv -f "${inbox}" "${note}"
    echo "==> inbox → notes: ${inbox} → ${note}"
  fi
}

promote_inbox "${ROOT}/inbox" "${ROOT}/notes"
shopt -s nullglob
for proj in "${ROOT}"/projects/*/ ; do
  [[ -d "${proj}" ]] || continue
  promote_inbox "${proj}inbox" "${proj}notes"
done
shopt -u nullglob

python3 "${ROOT}/scripts/publish.py"
echo "OK: ${ROOT}/site/index.html"
