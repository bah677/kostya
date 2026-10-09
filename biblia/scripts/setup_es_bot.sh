#!/usr/bin/env bash
#
# Подготовка второго бота (испанский) из того же кода.
#
# Испанский бот — это не копия проекта, а второй процесс: тот же каталог,
# свой .env.es (свой токен, своя база, BOT_LANG=es) и своя запись в
# supervisor. Так правки достаются обоим ботам.
#
# Что делает скрипт:
#   1) проверяет, что база biblia_es существует (создать её может только
#      суперпользователь — команду напечатает);
#   2) переносит в неё структуру базы русского бота, без данных;
#   3) создаёт .env.es рядом с .env, если его ещё нет.
#
# Запуск:
#   cd /home/appuser/biblia && bash scripts/setup_es_bot.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${ROOT}/venv/bin/python3"
ENV_RU="${ROOT}/.env"
ENV_ES="${ROOT}/.env.es"
DB_ES="${BIBLIA_ES_DB_NAME:-biblia_es}"

if [[ ! -f "${ENV_RU}" ]]; then
  echo "Нет ${ENV_RU} — запускать надо из каталога бота." >&2
  exit 1
fi

read -r DB_HOST DB_PORT DB_USER DB_NAME_RU DB_PASS <<<"$(
  cd "${ROOT}" && "${PY}" - <<'PYEOF'
import sys
sys.path.insert(0, ".")
from config import load_biblia_bot_config as L
c = L()
print(c.DB_HOST, c.DB_PORT or "5432", c.DB_USER, c.BIBLIA_DB_NAME, c.DB_PASSWORD)
PYEOF
)"
export PGPASSWORD="${DB_PASS}"

echo "==> база русского бота: ${DB_NAME_RU}, новая: ${DB_ES}"

exists=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname='${DB_ES}'" || true)

if [[ "${exists}" != "1" ]]; then
  cat >&2 <<EOF

База ${DB_ES} не существует, а прав на её создание у ${DB_USER} нет.
Выполните один раз под суперпользователем и запустите скрипт снова:

  sudo -u postgres psql -c "CREATE DATABASE ${DB_ES} OWNER ${DB_USER}"

EOF
  exit 2
fi

tables=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_ES}" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")

if [[ "${tables}" -gt 0 ]]; then
  echo "==> в ${DB_ES} уже ${tables} таблиц — структуру не трогаю"
else
  echo "==> переношу структуру из ${DB_NAME_RU} (только схема, без данных)"
  pg_dump -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" \
    --schema-only --no-owner --no-privileges "${DB_NAME_RU}" \
    | psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_ES}" -q
  tables=$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_ES}" -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
  echo "==> таблиц в ${DB_ES}: ${tables}"
fi

if [[ -f "${ENV_ES}" ]]; then
  echo "==> ${ENV_ES} уже есть — не перезаписываю"
else
  echo "==> создаю ${ENV_ES} из ${ENV_RU}"
  # Берём весь русский .env (общий Postgres, ключи моделей, ElevenLabs), но
  # вычищаем из копии то, что у второго бота своё, и дописываем свои значения
  # в КОНЕЦ.
  #
  # Порядок важен: при повторе ключа python-dotenv оставляет ПОСЛЕДНЕЕ
  # значение (проверено). Если положить переопределения сверху, испанский бот
  # молча возьмёт русский токен и русскую базу — и начнёт отвечать в чужом
  # боте. Поэтому и вычищаем, и дописываем снизу: две защиты от одного.
  {
    echo "# Испанский бот «Habla con Dios». Тот же код, второй процесс."
    echo "# Запуск: BOT_ENV_FILE=${ENV_ES} venv/bin/python3 main.py"
    echo "#"
    echo "# Скопировано из .env русского бота; своё — в конце файла."
    echo "# При повторе ключа dotenv берёт последнее значение."
    echo
    grep -vE '^[[:space:]]*(BOT_LANG|BIBLIA_BOT_TOKEN|BIBLIA_DB_NAME)=' "${ENV_RU}"
    echo
    echo "# ── своё у испанского бота ───────────────────────────────────────"
    echo "BOT_LANG=es"
    echo "BIBLIA_BOT_TOKEN=ПОДСТАВЬТЕ_ТОКЕН"
    echo "BIBLIA_DB_NAME=${DB_ES}"
  } > "${ENV_ES}"
  chmod 600 "${ENV_ES}"
  echo "    не забудьте вписать BIBLIA_BOT_TOKEN"
fi

echo
echo "Готово. Дальше — запись в supervisor:"
cat <<EOF

[program:biblia_es]
command=${ROOT}/venv/bin/python3 ${ROOT}/main.py
directory=${ROOT}
environment=BOT_ENV_FILE="${ENV_ES}"
user=appuser
autostart=true
autorestart=true
stderr_logfile=${ROOT}/log/biblia_es_errors.log
stdout_logfile=${ROOT}/log/biblia_es.log

EOF
