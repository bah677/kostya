#!/usr/bin/env bash
#
# Восстановить каталог log/ на проде из снимка club_code_*.tgz (тот же формат, что создаёт deploy_prod.sh).
#
# Использование:
#   ./scripts/restore_club_log_from_snapshot.sh /home/appuser/old_bots/club_deploy_snapshots/club_code_20260508_234817.tgz
#
# Последний снимок в каталоге по умолчанию:
#   ./scripts/restore_club_log_from_snapshot.sh --latest
#
# Переменные (опционально):
#   CLUB_PROD_ROOT=/home/appuser/club
#   CLUB_CODE_SNAPSHOTS_DIR=/home/appuser/old_bots/club_deploy_snapshots
#   SKIP_CONFIRM=1   — без запроса y/N
#   DEPLOY_RUN_USER=appuser — chown после распаковки (если запуск от root)
#
set -euo pipefail

CLUB_PROD_ROOT="${CLUB_PROD_ROOT:-/home/appuser/club}"
CLUB_CODE_SNAPSHOTS_DIR="${CLUB_CODE_SNAPSHOTS_DIR:-/home/appuser/old_bots/club_deploy_snapshots}"
SKIP_CONFIRM="${SKIP_CONFIRM:-0}"

die() { echo "ERROR: $*" >&2; exit 1; }

PROD_NAME="$(basename "${CLUB_PROD_ROOT}")"
PARENT="$(dirname "${CLUB_PROD_ROOT}")"
REL_LOG="${PROD_NAME}/log"
# В репозитории/на диске иногда встречается отдельный каталог «logs» (с s) — не путать с log/ из main.py
REL_LOGS="${PROD_NAME}/logs"

archive_list_has() {
  local pat="$1"
  tar -tzf "${ARCHIVE}" 2>/dev/null | grep -qE "$pat" || return 1
}

pick_latest_archive() {
  local d="${CLUB_CODE_SNAPSHOTS_DIR}"
  [[ -d "$d" ]] || die "нет каталога снимков: $d"
  local f
  f="$(ls -1t "${d}"/club_code_*.tgz 2>/dev/null | head -1)"
  [[ -n "$f" ]] || die "в $d нет club_code_*.tgz"
  echo "$f"
}

ARCHIVE=""

if [[ "${1:-}" == "--latest" ]]; then
  ARCHIVE="$(pick_latest_archive)"
  echo "==> выбран снимок: ${ARCHIVE}"
elif [[ -n "${1:-}" ]]; then
  ARCHIVE="$1"
else
  die "укажите путь к .tgz или --latest"
fi

[[ -f "$ARCHIVE" ]] || die "файл не найден: ${ARCHIVE}"

_have_log_dir=false
archive_list_has "^\./?${PROD_NAME}/log(/|\$)" && _have_log_dir=true
_have_logs_dir=false
archive_list_has "^\./?${REL_LOGS}(/|\$)" && _have_logs_dir=true

if [[ "${_have_log_dir}" != "true" ]]; then
  echo "WARN: в архиве нет каталога ${REL_LOG}/ (туда пишет бот: main.py → log/)." >&2
  if [[ "${_have_logs_dir}" == "true" ]]; then
    echo "      Зато есть ${REL_LOGS}/ — это другая папка (часто пустая/чужая), не log/ бота." >&2
  fi
  echo "      Содержимое с префиксом ${PROD_NAME}/:" >&2
  tar -tzf "${ARCHIVE}" 2>/dev/null | grep "${PROD_NAME}" | head -25 || true
fi

echo "==> Распаковать ${REL_LOG}/ → ${CLUB_PROD_ROOT}/log"
echo "    архив: ${ARCHIVE}"
if [[ "${SKIP_CONFIRM}" != "1" ]]; then
  read -r -p "Продолжить? [y/N] " ans || true
  [[ "${ans}" =~ ^[yY]([eE][sS])?$ ]] || { echo "отмена"; exit 0; }
fi

mkdir -p "${CLUB_PROD_ROOT}/log"

# В архиве пути вида club/log/... ; -C PARENT даёт /home/appuser/club/log
set +e
tar -xzf "${ARCHIVE}" -C "${PARENT}" "${REL_LOG}" 2>/dev/null
_x=$?
set -e
if [[ "${_x}" -ne 0 ]]; then
  if archive_list_has "^\./?${PROD_NAME}/log"; then
    die "tar вернул ошибку при распаковке ${REL_LOG} (права на ${PARENT} или битый архив?)"
  fi
  die "в архиве нет ${REL_LOG}/ — возьмите другой club_code_*.tgz (до порчи log/ или когда бот уже писал bot.log)"
fi

# Если в снимке не было файлов, только пустая log — скажем явно
if [[ -d "${CLUB_PROD_ROOT}/log" ]] && [[ -z "$(find "${CLUB_PROD_ROOT}/log" -type f -print -quit 2>/dev/null)" ]]; then
  echo "WARN: ${CLUB_PROD_ROOT}/log сейчас без файлов — в этом архиве не было bot.log / bot-errors.log." >&2
fi

if [[ "$(id -u)" -eq 0 ]]; then
  _u="${DEPLOY_RUN_USER:-${SUDO_USER:-appuser}}"
  chown -R "${_u}:${_u}" "${CLUB_PROD_ROOT}/log"
  echo "==> chown ${_u} → ${CLUB_PROD_ROOT}/log"
fi

echo "✅ Готово."
