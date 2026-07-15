#!/usr/bin/env bash
set -euo pipefail

# DB migrations runner:
# - loads env from .env
# - applies only not-yet-applied SQL migrations (schema_migrations table)
# - optionally runs legacy snapshot migration if LEGACY_ADMIN_DB_URL is set
#
# Usage:
#   bash scripts/apply_all_db_migrations.sh
#
# Optional env:
#   RUN_LEGACY_SNAPSHOT_MIGRATION=0   # skip python migration step
#   RUN_ATTRIBUTION_BACKFILL=0        # skip backfill attribution_touches from interaction_logs

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f ".env" ]]; then
  echo "❌ .env not found in $ROOT_DIR"
  exit 1
fi

set -a
source .env
set +a

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-}"
DB_USER="${DB_USER:-}"
DB_PASSWORD="${DB_PASSWORD:-}"

if [[ -z "$DB_NAME" || -z "$DB_USER" || -z "$DB_PASSWORD" ]]; then
  echo "❌ Missing DB creds in .env (need DB_NAME, DB_USER, DB_PASSWORD)"
  exit 1
fi

if ! command -v psql >/dev/null 2>&1; then
  echo "❌ psql is required"
  exit 1
fi

echo "==> Applying SQL migrations to ${DB_USER}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
export PGPASSWORD="$DB_PASSWORD"

psql_exec() {
  psql \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    -v ON_ERROR_STOP=1 \
    "$@"
}

ensure_schema_migrations_table() {
  psql_exec -c "
CREATE TABLE IF NOT EXISTS public.schema_migrations (
  filename TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);"
}

migration_applied() {
  local file="$1"
  local found=""
  found="$(psql_exec -tA -c "SELECT 1 FROM public.schema_migrations WHERE filename = '${file}' LIMIT 1;")"
  [[ "$found" == "1" ]]
}

run_migration() {
  local file="$1"
  if [[ ! -f "$file" ]]; then
    echo "❌ Missing migration: $file"
    exit 1
  fi
  if migration_applied "$file"; then
    echo "   -> $file (already applied, skip)"
    return
  fi
  echo "   -> $file (apply)"
  psql_exec -f "$file"
  psql_exec -c "INSERT INTO public.schema_migrations(filename) VALUES ('${file}');"
}

ensure_schema_migrations_table

shopt -s nullglob
migration_files=(migrations/[0-9][0-9][0-9]_*.sql)
shopt -u nullglob

if [[ "${#migration_files[@]}" -eq 0 ]]; then
  echo "❌ No SQL migrations found in migrations/"
  exit 1
fi

for file in "${migration_files[@]}"; do
  run_migration "$file"
done

echo "✅ SQL migrations applied"

RUN_ATTRIBUTION_BACKFILL="${RUN_ATTRIBUTION_BACKFILL:-1}"
if [[ "$RUN_ATTRIBUTION_BACKFILL" == "1" ]]; then
  echo "==> Backfill attribution_touches (interaction_logs)"
  if [[ -x "./venv/bin/python" ]]; then
    ./venv/bin/python scripts/backfill_attribution_touches.py
  else
    python3 scripts/backfill_attribution_touches.py
  fi
  echo "✅ Attribution backfill finished"
else
  echo "ℹ️ Attribution backfill skipped (RUN_ATTRIBUTION_BACKFILL=0)"
fi

RUN_LEGACY_SNAPSHOT_MIGRATION="${RUN_LEGACY_SNAPSHOT_MIGRATION:-1}"
LEGACY_ADMIN_DB_URL="${LEGACY_ADMIN_DB_URL:-}"

if [[ "$RUN_LEGACY_SNAPSHOT_MIGRATION" == "1" && -n "$LEGACY_ADMIN_DB_URL" ]]; then
  echo "==> Running legacy snapshot migration"
  if [[ -x "./venv/bin/python" ]]; then
    ./venv/bin/python scripts/migrate_legacy_club_snapshots.py
  else
    python3 scripts/migrate_legacy_club_snapshots.py
  fi
  echo "✅ Legacy snapshot migration finished"
else
  echo "ℹ️ Legacy snapshot migration skipped"
  echo "   (set LEGACY_ADMIN_DB_URL and RUN_LEGACY_SNAPSHOT_MIGRATION=1 to enable)"
fi

unset PGPASSWORD
echo "🎉 Done"
