#!/usr/bin/env bash
# Остановка prod-копий клубного бота (без автоперезапуска supervisord).
#
#   ./scripts/stop_prod.sh          # club + nastya
#   ./scripts/stop_prod.sh club     # только /home/appuser/club
#   ./scripts/stop_prod.sh nastya   # только /home/appuser/club_nastya
#
set -euo pipefail

if [[ "${SKIP_SUDO_REEXEC:-0}" != "1" && "$(id -u)" -ne 0 ]]; then
  echo "==> Нужны права root — перезапуск через sudo..."
  exec sudo -E SKIP_SUDO_REEXEC=1 "$(command -v bash)" "${BASH_SOURCE[0]}" "$@"
fi

TARGET="${1:-all}"

stop_one() {
  local prog="$1"
  echo "==> supervisorctl stop ${prog}"
  supervisorctl stop "${prog}" || echo "WARN: не удалось остановить ${prog}" >&2
}

case "${TARGET}" in
  club)
    stop_one "bots:club_bot"
    ;;
  nastya|club_nastya)
    stop_one "club_nastya:club_nastya_bot"
    ;;
  all)
    stop_one "bots:club_bot"
    stop_one "club_nastya:club_nastya_bot"
    ;;
  *)
    echo "Неизвестный target: ${TARGET}. Используйте: club | nastya | all" >&2
    exit 1
    ;;
esac

echo "==> Статус:"
supervisorctl status bots:club_bot club_nastya:club_nastya_bot 2>/dev/null || true
echo "==> Процессы main.py:"
pgrep -af '/home/appuser/club.*/main.py' || echo "(нет)"
