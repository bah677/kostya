#!/usr/bin/env bash
#
# Шаг 0 перед миграциями club_db на проде: логический дамп в custom-формате (pg_restore).
#
# Запуск на сервере (нужен доступ sudo -u postgres, как для миграций через deploy_prod --sql):
#
#   ./scripts/backup_club_db_prod.sh
#   CLUB_DB=club_db DUMP_ROOT=/home/appuser/backups/club/db ./scripts/backup_club_db_prod.sh
#
# Переменные окружения:
#   CLUB_DB    имя базы (по умолчанию club_db)
#   DUMP_ROOT  каталог для файлов (по умолчанию /home/appuser/backups/club/db)
#
set -euo pipefail

DB_NAME=${CLUB_DB:-club_db}
DUMP_ROOT=${DUMP_ROOT:-/home/appuser/backups/club/db}

mkdir -p "$DUMP_ROOT"
ts=$(date +%Y%m%d_%H%M%S)
out="$DUMP_ROOT/${DB_NAME}_${ts}.dump"

echo "Дамп БД: $DB_NAME → $out"
sudo -u postgres pg_dump -Fc -d "$DB_NAME" >"$out"
ls -lh "$out"
echo "Готово. Восстановление (пример): sudo -u postgres pg_restore -d ${DB_NAME}_restored -c $out"
