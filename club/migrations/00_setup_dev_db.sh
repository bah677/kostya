#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 00_setup_dev_db.sh — РАЗОВЫЙ скрипт.
#
# Создаёт dev-копию боевой БД club_db под именем club_db_dev на том же
# Postgres-инстансе. Прод (club_db) не затрагивается — операция read-only
# для исходной базы (используется pg_dump через MVCC-снэпшот).
#
# !!! Запускать ОДИН раз, на хосте, где стоит Postgres.
# !!! На прод НЕ катить — это служебный скрипт окружения разработки.
#
# Использование:
#   sudo bash migrations/00_setup_dev_db.sh
#
# Внутри сам перевыполнится как sudo -u postgres и подключится к Postgres
# через unix-сокет (peer auth) — пароль postgres не нужен.
#
# Поведение:
#   * Если club_db_dev уже существует — спросит, дропнуть и пересоздать или нет.
#   * После завершения покажет размер обеих БД для сверки.
# ---------------------------------------------------------------------------
set -euo pipefail

SRC_DB="club_db"
DEV_DB="club_db_dev"
APP_USER="club_db_user"
PG_PORT="5432"

# --- Проверки окружения ----------------------------------------------------
if ! command -v psql >/dev/null || ! command -v pg_dump >/dev/null; then
  echo "[!] Не нашёл psql/pg_dump. Установи postgresql-client." >&2
  exit 1
fi

# Перевыполнение под пользователем postgres (peer-auth по unix-сокету).
if [[ "$(id -un)" != "postgres" ]]; then
  if [[ "${EUID}" -eq 0 ]]; then
    exec sudo -u postgres bash "$0" "$@"
  else
    echo "[!] Запусти от root, я сам перевыполнюсь под postgres:"
    echo "      sudo bash $0"
    exit 1
  fi
fi

# Подключение через unix-сокет (без -h) — peer auth для postgres.
PSQL="psql -X -v ON_ERROR_STOP=1 -p ${PG_PORT}"

echo "[i] Подключение к Postgres (peer auth, unix socket)..."
${PSQL} -d postgres -tAc "SELECT version();" | head -1

# --- Если dev-БД уже есть, спросить подтверждение -------------------------
EXISTS=$(${PSQL} -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname='${DEV_DB}'" || true)
if [[ "${EXISTS}" == "1" ]]; then
  echo
  echo "[!] База ${DEV_DB} уже существует."
  read -r -p "    Дропнуть и пересоздать заново? [y/N] " ans
  if [[ "${ans,,}" != "y" ]]; then
    echo "    Прерываюсь, ничего не меняю."
    exit 0
  fi
  echo "[i] Отключаю активные сессии и дропаю ${DEV_DB}..."
  ${PSQL} -d postgres -c "
    SELECT pg_terminate_backend(pid)
    FROM pg_stat_activity
    WHERE datname='${DEV_DB}' AND pid <> pg_backend_pid();
  " >/dev/null
  ${PSQL} -d postgres -c "DROP DATABASE ${DEV_DB};"
fi

# --- Создаём пустую dev-БД ------------------------------------------------
echo "[i] Создаю пустую ${DEV_DB} (owner=${APP_USER})..."
${PSQL} -d postgres -c "
  CREATE DATABASE ${DEV_DB}
    WITH OWNER = ${APP_USER}
         ENCODING = 'UTF8'
         TEMPLATE = template0;
"

# --- Заливаем содержимое club_db в club_db_dev ----------------------------
echo "[i] Снимаю pg_dump --format=custom из ${SRC_DB} и заливаю в ${DEV_DB}..."
DUMP_FILE="$(mktemp -t club_db_XXXXXX.dump)"
trap 'rm -f "${DUMP_FILE}"' EXIT

pg_dump -p "${PG_PORT}" \
  --format=custom --no-owner --no-acl --serializable-deferrable \
  -d "${SRC_DB}" -f "${DUMP_FILE}"

pg_restore -p "${PG_PORT}" \
  --no-owner --no-acl --role="${APP_USER}" --exit-on-error \
  -d "${DEV_DB}" "${DUMP_FILE}"

# --- Гарантируем, что прикладной юзер реально владелец всех объектов ------
echo "[i] Передаю владение всеми объектами ${DEV_DB} → ${APP_USER}..."
${PSQL} -d "${DEV_DB}" -c "REASSIGN OWNED BY postgres TO ${APP_USER};" >/dev/null || true
${PSQL} -d "${DEV_DB}" -c "
  GRANT ALL PRIVILEGES ON DATABASE ${DEV_DB} TO ${APP_USER};
  GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA public TO ${APP_USER};
  GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO ${APP_USER};
  GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO ${APP_USER};
"

# --- Проверка размеров -----------------------------------------------------
echo
echo "[i] Размеры баз:"
${PSQL} -d postgres -c "
  SELECT datname, pg_size_pretty(pg_database_size(datname)) AS size
  FROM pg_database WHERE datname IN ('${SRC_DB}','${DEV_DB}')
  ORDER BY 1;
"

# --- Проверка количества таблиц --------------------------------------------
echo "[i] Сверка количества таблиц:"
SRC_TBL=$(${PSQL} -d "${SRC_DB}" -tAc "SELECT count(*) FROM pg_tables WHERE schemaname='public';")
DEV_TBL=$(${PSQL} -d "${DEV_DB}" -tAc "SELECT count(*) FROM pg_tables WHERE schemaname='public';")
echo "    ${SRC_DB}: ${SRC_TBL}    ${DEV_DB}: ${DEV_TBL}"

echo
echo "[OK] Готово. Теперь:"
echo "  1) В .env проекта поменяй DB_NAME=${DEV_DB}"
echo "     (а так же MIRON_BOT_TOKEN — на токен ТЕСТового бота, чтобы не флудить в проде)"
echo "  2) Прод-бот продолжает работать с ${SRC_DB} — его трогать не надо."
echo "  3) Все дальнейшие правки схемы фиксируй файлами migrations/NNN_*.sql"
