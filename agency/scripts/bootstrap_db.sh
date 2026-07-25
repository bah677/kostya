#!/bin/bash
# Создать БД agency + роль (нужен sudo postgres). Запускает пользователь.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PW_FILE="$ROOT/scripts/.agency_db_password"
if [[ ! -f "$PW_FILE" ]]; then
  PW="ag_$(openssl rand -base64 12 | tr -dc 'a-zA-Z0-9' | head -c 16)"
  echo "$PW" > "$PW_FILE"
  chmod 600 "$PW_FILE"
else
  PW="$(cat "$PW_FILE")"
fi

echo "Creating role agency_user / DB agency (password in $PW_FILE)…"
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agency_user') THEN
    CREATE ROLE agency_user LOGIN PASSWORD '${PW}';
  ELSE
    ALTER ROLE agency_user WITH LOGIN PASSWORD '${PW}';
  END IF;
END\$\$;
SELECT 'ok_role';
SQL

sudo -u postgres psql -v ON_ERROR_STOP=1 -c "SELECT 1 FROM pg_database WHERE datname='agency'" | grep -q 1 \
  || sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE DATABASE agency OWNER agency_user;"

sudo -u postgres psql -v ON_ERROR_STOP=1 -d agency -c "GRANT ALL ON SCHEMA public TO agency_user;"

# Optional RO role for ecosystem (password = same file with _ro suffix unless set)
RO_PW="${AGENCY_RO_PASSWORD:-${PW}_ro}"
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agency_ro') THEN
    CREATE ROLE agency_ro LOGIN PASSWORD '${RO_PW}';
  END IF;
END\$\$;
SQL

for DB in biblia_bot club_db biblia_bot_dev club_db_dev; do
  if sudo -u postgres psql -Atc "SELECT 1 FROM pg_database WHERE datname='${DB}'" | grep -q 1; then
    echo "Grant SELECT on ${DB} to agency_ro…"
    sudo -u postgres psql -d "$DB" -v ON_ERROR_STOP=1 <<SQL
GRANT CONNECT ON DATABASE ${DB} TO agency_ro;
GRANT USAGE ON SCHEMA public TO agency_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO agency_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO agency_ro;
SQL
  fi
done

echo "Done. Put into .env:"
echo "  AGENCY_DB_USER=agency_user"
echo "  AGENCY_DB_PASSWORD=${PW}"
echo "  (optional later) BIBLIA/CLUB *_USER=agency_ro  password=${RO_PW}"
