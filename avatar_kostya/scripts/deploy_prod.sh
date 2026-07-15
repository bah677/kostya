#!/usr/bin/env bash
#
# Накат кода dev → prod на одном сервере (без git).
# Пара для зеркала прода в dev: scripts/sync_prod_to_dev.sh
#
#   Источник:      /home/appuser/bog/biblia
#   Назначение:     /home/appuser/biblia  (supervisor: biblia_bot)
#
# Шаги (после синхронизации dev с prod деревом файлов — см. ниже):
#   1) tar-архив текущего кода на проде (БЕЗ venv, data, log, кэша, локальных sqlite сессий)
#   2) rsync dev → prod
#   3) опционально: один или несколько .sql через psql под postgres (sudo спросит пароль)
#   4) supervisorctl restart biblia_bot
#
# Примеры:
#   ./scripts/deploy_prod.sh --dry-run
#   ./scripts/deploy_prod.sh
#   ./scripts/deploy_prod.sh --sql migrations/biblia/pg_001_013/013_messages_processing_time_ms.sql
#   ./scripts/deploy_prod.sh --db biblia_bot --sql migrations/biblia/some_patch.sql \
#                              --sql migrations/biblia/other.sql
#   ./scripts/deploy_prod.sh --no-restart
#   ./scripts/deploy_prod.sh --no-backup        # только если архив точно не нужен
#
# Переменные окружения:
#   SUPERVISOR_NAME   программа supervisor (по умолчанию biblia_bot)
#   ROLLBACK_ROOT    каталог для tar-снимков (по умолчанию ~/old_bots/biblia_deploy_snapshots)
#   BIBLIA_DB        имя базы для --sql (по умолчанию biblia_bot)
#
set -euo pipefail

SRC=/home/appuser/bog/biblia
DST=/home/appuser/biblia
ROLLBACK_ROOT=${ROLLBACK_ROOT:-/home/appuser/old_bots/biblia_deploy_snapshots}
SUPERVISOR_NAME=${SUPERVISOR_NAME:-bots:biblia_bot}
DB_NAME=${BIBLIA_DB:-biblia_bot}

DRY_RUN=()
DO_RESTART=1
DO_BACKUP=1
WITH_DATA=0
declare -a SQL_FILES=()

usage() {
  sed -n '2,42p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)       DRY_RUN=(--dry-run); shift ;;
    --no-restart)    DO_RESTART=0; shift ;;
    --no-backup)     DO_BACKUP=0; shift ;;
    --with-data)     WITH_DATA=1; shift ;;
    --db)
      DB_NAME="${2:?укажите имя базы после --db}"
      shift 2
      ;;
    --sql)
      SQL_FILES+=("${2:?укажите путь к .sql после --sql}")
      shift 2
      ;;
    -h|--help)       usage ;;
    *)
      echo "Неизвестный аргумент: $1 (используйте --help)" >&2
      exit 1
      ;;
  esac
done

if [[ ! -d "$SRC" ]]; then
  echo "Нет каталога dev: $SRC" >&2
  exit 1
fi
if [[ ! -d "$DST" ]]; then
  echo "Нет каталога prod: $DST" >&2
  exit 1
fi

RSYNC_BASE=(rsync -a -h --info=stats1)
RSYNC_EXCLUDES=(
  --exclude '__pycache__/'
  --exclude '*.pyc'
  --exclude '.env'
  --exclude '.env.*'
  --exclude '*.log'
  --exclude '*.db'
)

snapshot_prod() {
  local ts archive items
  mkdir -p "$ROLLBACK_ROOT"
  ts=$(date +%Y%m%d_%H%M%S)
  archive="$ROLLBACK_ROOT/biblia_code_${ts}.tgz"
  items=()
  for d in bot storage openai_client migrations scripts; do
    if [[ -d "$DST/$d" ]]; then
      items+=("$d")
    fi
  done
  [[ -f "$DST/README.txt" ]] && items+=("README.txt")
  [[ -f "$DST/requirements.txt" ]] && items+=("requirements.txt")
  shopt -s nullglob
  for p in "$DST"/*.py; do
    [[ -f "$p" ]] && items+=("$(basename "$p")")
  done
  shopt -u nullglob
  if [[ ${#items[@]} -eq 0 ]]; then
    echo "Не найдено что архивировать в $DST — пропуск снимка." >&2
    return 0
  fi
  (
    cd "$DST"
    tar -czf "$archive" "${items[@]}"
  )
  echo "Резервная копия (код без venv/data/log/db): $archive"
}

sync_dir() {
  local name="$1"
  if [[ ! -d "$SRC/$name" ]]; then
    return 0
  fi
  echo "==> $name/"
  "${RSYNC_BASE[@]}" "${DRY_RUN[@]}" "${RSYNC_EXCLUDES[@]}" \
    "$SRC/$name/" "$DST/$name/"
}

echo "Деплой: $SRC  →  $DST"

if [[ ${#DRY_RUN[@]} -eq 0 && "$DO_BACKUP" -eq 1 ]]; then
  snapshot_prod
elif [[ ${#DRY_RUN[@]} -eq 0 && "$DO_BACKUP" -eq 0 ]]; then
  echo "(резервная копия отключена --no-backup)"
fi

for d in bot storage openai_client migrations scripts; do
  sync_dir "$d"
done

if [[ "$WITH_DATA" -eq 1 ]]; then
  sync_dir data
fi

if [[ -f "$SRC/README.txt" ]]; then
  echo "==> README.txt"
  "${RSYNC_BASE[@]}" "${DRY_RUN[@]}" "$SRC/README.txt" "$DST/README.txt"
fi

if [[ -f "$SRC/requirements.txt" ]]; then
  echo "==> requirements.txt"
  "${RSYNC_BASE[@]}" "${DRY_RUN[@]}" "$SRC/requirements.txt" "$DST/requirements.txt"
fi

if [[ -f "$SRC/.gitignore" ]]; then
  echo "==> .gitignore"
  "${RSYNC_BASE[@]}" "${DRY_RUN[@]}" "$SRC/.gitignore" "$DST/.gitignore"
fi

shopt -s nullglob
for f in "$SRC"/*.py; do
  echo "==> $(basename "$f")"
  "${RSYNC_BASE[@]}" "${DRY_RUN[@]}" "$f" "$DST/"
done
shopt -u nullglob

if [[ ${#DRY_RUN[@]} -ne 0 ]]; then
  echo "Это был --dry-run; prod не изменяли, резервную копию и psql не делали."
  exit 0
fi

for rel in "${SQL_FILES[@]}"; do
  f="$rel"
  [[ "$f" = /* ]] || f="$DST/$rel"
  if [[ ! -f "$f" ]]; then
    echo "Нет файла для миграции: $f" >&2
    exit 1
  fi
  echo "==> psql ON_ERROR_STOP: $DB_NAME ← $f"
  sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME" -f "$f"
done

if [[ "$DO_RESTART" -eq 1 ]]; then
  echo "==> supervisorctl restart $SUPERVISOR_NAME"
  sudo supervisorctl restart "$SUPERVISOR_NAME"
  sudo supervisorctl status "$SUPERVISOR_NAME" || true
fi

echo "Готово."
