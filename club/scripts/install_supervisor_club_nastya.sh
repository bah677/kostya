#!/usr/bin/env bash
# Однократная установка program+group club_nastya в supervisor.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Запустите от root: sudo bash scripts/install_supervisor_club_nastya.sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${SCRIPT_DIR}/supervisor/club_nastya.conf"
DEST="/etc/supervisor/conf.d/club_nastya.conf"

[[ -f "${SRC}" ]] || { echo "Нет ${SRC}"; exit 1; }

cp "${SRC}" "${DEST}"
chmod 644 "${DEST}"
echo "==> Установлено: ${DEST}"

supervisorctl reread
supervisorctl update club_nastya

echo "==> Статус:"
supervisorctl status club_nastya:club_nastya_bot || true
echo ""
echo "Перед первым start: каталог /home/appuser/club_nastya с .env и venv (см. deploy_prod.sh)."
