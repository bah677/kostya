#!/usr/bin/env bash
# =============================================================================
# Прод: Biblia Postgres → нормализованная схема club_ai (монорепо).
#
# Перед первым запуском прочтите APPLY.txt и отредактируйте переменные ниже.
#
# Общая схема (две роли):
#   1) Суперпользователь Postgres (локально почти всегда: sudo -u postgres psql)
#      — передать OWNER рассылок на роль приложения (bot_user), если после
#        pg_restore/template владелец mailing_* остался postgres.
#   2) Роль приложения (bot_user и тот же пользователь для миграций)
#      — весь пайплайн из apply_normalize_biblia_to_club_ai.sh +
#      — после шага супера: 012 и biblia/002 (уже включены в apply_normalize).
#
# Рекомендуемые окно и бэкап:
#   - Сделать логический бэкап БД перед миграцией.
#   - Идеально короткий maintenance: остановить ботов/воркеры, записывающие в БД,
#     затем pg_dump → миграция → смоук-тест → поднять сервисы.
#   - Миграции идемпотентны там, где отмечено в SQL (ADD IF NOT EXISTS, и т.д.),
#     но прод всё равно делайте с бэкапом.
#
# Пример переменных (подставьте хост, имя базы прод, пользователя приложения):
#   export PGHOST=127.0.0.1
#   export PGPORT=5432
#   export PGDATABASE=telegram_bot          # прод-имя БД Biblia
#   export PGUSER=bot_user
#   export PGPASSWORD='***'                  # или .pgpass, не экспортируя в историю
#
# Запуск ролей приложения:
#   bash migrations/biblia/normalize_to_club_ai/apply_biblia_normalize_production.sh migrate
#
# Только проверить подключение:
#   bash .../apply_biblia_normalize_production.sh ping
#
# Откат: восстановление из дампа созданного PHASE_BACKUP ниже или снапшот диска.
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
NORM_DIR="$REPO_ROOT/migrations/biblia/normalize_to_club_ai"
APPLY_SQL="$NORM_DIR/apply_normalize_biblia_to_club_ai.sh"
GRANT_PUBLIC_SQL="$NORM_DIR/99_grant_app_role_public_schema.sql"

ACTION="${1:-}"

require_app_conn() {
  : "${PGDATABASE:?Укажите PGDATABASE (прод-база Biblia, например telegram_bot)}"
  : "${PGUSER:?Укажите PGUSER (роль приложения, например bot_user)}"
}

usage() {
  sed -n '2,40p' "$0" | grep -E '^#' | sed 's/^# \{0,1\}//'
}

ping_db() {
  psql -v ON_ERROR_STOP=1 "${PGOPTS[@]}" -c 'SELECT current_database(), current_user, NOW();'
}

migrate_as_app_role() {
  echo "=============================="
  echo "==> apply_normalize_biblia_to_club_ai.sh (полный пайплайн)"
  echo "=============================="
  bash "$APPLY_SQL"
}

phase_backup_hint() {
  local ts
  ts="$(date -u +%Y%m%dT%HZ)"
  echo ""
  echo "-------- PHASE_BACKUP (выполните вручную на прод-хосте) --------"
  echo "Пример дампа перед миграцией:"
  echo "  mkdir -p /var/backups/postgres/biblia_migrate_$ts"
  echo "  pg_dump -h \"\${PGHOST:-}\" -p \"\${PGPORT:-5432}\" -U postgres -Fc \\\\"
  echo "    --no-owner --no-privileges \\\\"
  echo "    -f \"/var/backups/postgres/biblia_migrate_$ts/\${PGDATABASE}.dump\" \\\\"
  echo "    \"\$PGDATABASE\""
  echo "(Или свой путь политики бэкапов; для супера часто нужен -U postgres / peer.)"
  echo "----------------------------------------------------------------"
  echo ""
}

phase_superuser_grants_public() {
  local db="${PGDATABASE:-<YOUR_PROD_DATABASE>}"
  echo ""
  echo "-------- PHASE_SUPERUSER_GRANT_PUBLIC_SCHEMA --------------------------"
  echo "При «permission denied for table …» после клонирования: выдайте права роли приложения"
  echo "(то же имя, что DB_USER в .env). Отредактируйте app_role внутри файла:"
  echo ""
  echo "  sudo -u postgres psql -d \"$db\" -v ON_ERROR_STOP=1 \\\\"
  echo "    -f \"$GRANT_PUBLIC_SQL\""
  echo ""
  echo "Разные имена баз (club / biblia в .env): прогоните этот файл по каждой БД раз."
  echo "-----------------------------------------------------------------------"
}

phase_superuser_owner_mailing() {
  local db="${PGDATABASE:-<YOUR_PROD_DATABASE>}"
  echo ""
  echo "-------- PHASE_SUPERUSER_OWNER_MAILING --------------------------------"
  echo "Если при ALTER в apply-скрипте видите «must be owner of table mailing_*»,"
  echo "один раз от суперпользователя (локально через peer):"
  echo ""
  echo "  sudo -u postgres psql -d \"$db\" -v ON_ERROR_STOP=1 \\\\"
  echo "    -f \"$OWNER_MAILING_SQL\""
  echo ""
  echo "После успешной смены владельца снова выполните этот же скрипт с аргументом migrate."
  echo "-----------------------------------------------------------------------"
}

build_pgopts() {
  PGOPTS=(-v ON_ERROR_STOP=1)
  [[ -n "${PGHOST:-}" ]] && PGOPTS+=(-h "$PGHOST")
  [[ -n "${PGPORT:-}" ]] && PGOPTS+=(-p "$PGPORT")
  [[ -n "${PGUSER:-}" ]] && PGOPTS+=(-U "$PGUSER")
  [[ -n "${PGDATABASE:-}" ]] && PGOPTS+=(-d "$PGDATABASE")
}

case "${ACTION}" in
  ping)
    require_app_conn
    build_pgopts
    ping_db
    ;;
  migrate)
    require_app_conn
    phase_backup_hint
    migrate_as_app_role
    echo ""
    echo "Готово. Проверьте смоук: бот, рассылки, INSERT в mailing_audience/logs."
    ;;
  hints)
    phase_backup_hint
    phase_superuser_grants_public
    phase_superuser_owner_mailing
    ;;
  help|-h|--help|"")
    usage
    ;;
  *)
    echo "Неизвестная команда: $ACTION (используйте: migrate | ping | hints | help)" >&2
    exit 1
    ;;
esac
