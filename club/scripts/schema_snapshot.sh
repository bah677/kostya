#!/usr/bin/env bash
# Снимок схемы PostgreSQL для сравнения prod и dev.
#
# Примеры:
#   sudo -u postgres ./scripts/schema_snapshot.sh club_db        > schema_prod.txt
#   sudo -u postgres ./scripts/schema_snapshot.sh club_db_dev    > schema_dev.txt
#   diff -u schema_prod.txt schema_dev.txt
#
# Либо из интерактивного psql:
#   \i /path/to/club/scripts/schema_snapshot.sql
#
set -euo pipefail

DB="${1:-}"
if [[ -z "$DB" ]]; then
  echo "Usage: $0 <database_name>" >&2
  echo "Example: sudo -u postgres $0 club_db > schema_club_db.txt" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SQL="$ROOT/scripts/schema_snapshot.sql"

if [[ ! -f "$SQL" ]]; then
  echo "Missing $SQL" >&2
  exit 1
fi

export PGCLIENTENCODING=UTF8
psql -v ON_ERROR_STOP=1 -d "$DB" -f "$SQL"
