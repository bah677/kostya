#!/usr/bin/env bash
#
# Строгий деплой кода dev → prod (Biblia) на одном сервере.
# После успешного деплоя — коммит и push dev-кода в GitHub (см. git_push_deploy.sh).
#
#   Источник:    /home/appuser/dev/kostya/biblia
#   Назначение:  /home/appuser/biblia        (supervisor: bots:biblia_bot)
#
# Шаги:
#   1) supervisorctl stop bots:biblia_bot
#   2) tar-снимок прод-кода в /home/appuser/backups/biblia/code/
#      (без data/, log/, venv/, .pyc, __pycache__; хранение 7 дней)
#   3) pg_dump БД biblia_bot в custom-формате в
#      /home/appuser/backups/biblia/db/biblia_db_<TS>.dump
#   4) rsync dev → prod, ЗЕРКАЛО (--delete) кроме data/, log/, venv/, .env
#   5) pip install -r requirements.txt в /home/appuser/biblia/venv
#      (если есть; обновлять/создавать venv этот скрипт сам не будет)
#   6) накат миграций — все .sql из migrations/biblia/, которые есть в dev,
#      но отсутствуют в проде на момент старта деплоя
#   7) supervisorctl start bots:biblia_bot
#
# Запуск:
#   sudo ./scripts/deploy_prod.sh         # пароль спросится один раз
#   ./scripts/deploy_prod.sh              # тогда sudo -v будет в начале
#
# Безопасный порядок работы:
#   1) Все правки — только в /home/appuser/dev/kostya/biblia
#   2) python3 -m py_compile … или import main в dev venv (опционально)
#   3) sudo ./scripts/deploy_prod.sh
#   Скрипт сам: бэкап prod → rsync → smoke-test import → миграции → start
#
# Откат при сбое (если бот не поднялся):
#   sudo supervisorctl stop bots:biblia_bot
#   cd /home/appuser/biblia && sudo tar -xzf /home/appuser/backups/biblia/code/biblia_code_<TS>.tgz
#   sudo supervisorctl start bots:biblia_bot
#
# Переменные окружения (можно переопределить):
#   SUPERVISOR_NAME    программа supervisor       (по умолчанию bots:biblia_bot)
#   BIBLIA_DB          имя базы для pg_dump/psql  (по умолчанию biblia_bot)
#   APP_USER           владелец venv в проде      (по умолчанию appuser)
#   SKIP_GIT_PUSH=1    не пушить код в GitHub после деплоя
#   GIT_REMOTE_URL     remote (по умолчанию git@github.com:bah677/kostya.git)
#   GIT_BRANCH         ветка (по умолчанию main)
#

set -euo pipefail

SRC=/home/appuser/dev/kostya/biblia
DST=/home/appuser/biblia

SUPERVISOR_NAME=${SUPERVISOR_NAME:-bots:biblia_bot}
DB_NAME=${BIBLIA_DB:-biblia_bot}
APP_USER=${APP_USER:-appuser}
SKIP_GIT_PUSH=${SKIP_GIT_PUSH:-0}
GIT_REMOTE_URL=${GIT_REMOTE_URL:-git@github.com:bah677/kostya.git}

CODE_SNAPS=${BIBLIA_CODE_SNAPSHOTS_DIR:-/home/appuser/backups/biblia/code}
DB_DUMPS=${BIBLIA_DB_DUMPS_DIR:-/home/appuser/backups/biblia/db}
PROD_VENV="$DST/venv"

TS=$(date +%Y%m%d_%H%M%S)
CODE_ARCHIVE="$CODE_SNAPS/biblia_code_${TS}.tgz"
DB_ARCHIVE="$DB_DUMPS/biblia_db_${TS}.dump"

# ---------- 0. sudo один раз ----------
if [[ $EUID -ne 0 ]]; then
  echo "==> sudo: введите пароль один раз"
  sudo -v
  ( while true; do sudo -n true; sleep 50; done ) &
  SUDO_KEEP_PID=$!
  trap 'kill "$SUDO_KEEP_PID" 2>/dev/null || true' EXIT
fi

[[ -d "$SRC" ]] || { echo "Нет каталога dev: $SRC" >&2; exit 1; }
[[ -d "$DST" ]] || { echo "Нет каталога prod: $DST" >&2; exit 1; }

REQUIRED_SRC=(
  main.py
  bot_app.py
  config.py
  command_handlers.py
  requirements.txt
  bot/handlers/messages.py
  bot/base_app.py
  bot/features/messaging.py
  bot/features/scripture_messaging.py
  bot/payments/payment_checker.py
)
missing=()
for f in "${REQUIRED_SRC[@]}"; do
  [[ -f "$SRC/$f" ]] || missing+=("$f")
done
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "ОШИБКА: в dev не хватает обязательных файлов — rsync --delete сломает prod:" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  echo "Сначала синхронизируйте prod → dev или восстановите файлы в dev." >&2
  exit 1
fi

count_py() {
  find "$1" -name '*.py' ! -path '*/venv/*' 2>/dev/null | wc -l
}
SRC_PY=$(count_py "$SRC")
DST_PY=$(count_py "$DST")
if [[ "$SRC_PY" -lt "$DST_PY" ]]; then
  echo "ОШИБКА: в dev меньше .py-файлов ($SRC_PY), чем в prod ($DST_PY)." >&2
  echo "rsync --delete удалит лишние файлы из prod. Синхронизируйте prod → dev." >&2
  exit 1
fi

echo "Деплой: $SRC → $DST  (TS=$TS)"
echo "    dev .py: $SRC_PY, prod .py: $DST_PY"

# ---------- 1. stop ----------
echo "==> supervisorctl stop $SUPERVISOR_NAME"
sudo supervisorctl stop "$SUPERVISOR_NAME"

# ---------- 2. tar прод-кода ----------
echo "==> tar снимок прод-кода"
sudo mkdir -p "$CODE_SNAPS"
sudo tar \
  --exclude='./data' \
  --exclude='./log' \
  --exclude='./venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  -czf "$CODE_ARCHIVE" \
  -C "$DST" .
echo "    $CODE_ARCHIVE"

# ---------- 3. pg_dump ----------
echo "==> pg_dump $DB_NAME (custom format)"
sudo mkdir -p "$DB_DUMPS"
# postgres должен иметь право записи (иначе Permission denied на --file)
sudo chown postgres:postgres "$DB_DUMPS"
sudo chmod 755 "$DB_DUMPS"
sudo -u postgres pg_dump \
  --format=custom \
  --no-owner \
  --file="$DB_ARCHIVE" \
  "$DB_NAME"
echo "    $DB_ARCHIVE"

# ---------- 3.0 retention: бэкапы 7д, data 7д, log/arc 30д ----------
RETENTION_SH=/home/appuser/dev/kostya/scripts/disk_retention.sh
if [[ -x "$RETENTION_SH" ]]; then
  echo "==> disk retention (backups/data 7d, logs 30d)"
  BACKUP_DAYS=7 DATA_DAYS=7 LOG_ARC_DAYS=30 \
    "$RETENTION_SH" deploy --apply || true
else
  echo "    (skip retention: $RETENTION_SH missing)"
fi

# ---------- 3.1. список новых миграций ДО rsync ----------
mapfile -t DEV_MIGRATIONS < <(cd "$SRC" && \
  find migrations/biblia -type f -name '*.sql' 2>/dev/null | sort)
mapfile -t PROD_MIGRATIONS < <(cd "$DST" && \
  find migrations/biblia -type f -name '*.sql' 2>/dev/null | sort)

NEW_MIGRATIONS=()
for m in "${DEV_MIGRATIONS[@]}"; do
  if ! printf '%s\n' "${PROD_MIGRATIONS[@]}" | grep -Fxq -- "$m"; then
    NEW_MIGRATIONS+=("$m")
  fi
done

# ---------- 4. rsync dev → prod (ЗЕРКАЛО) ----------
# --delete: всё, чего нет в dev, удаляется из prod.
# Исключённые пути (data/, log/, venv/, .env) защищены и от копирования, и от удаления.
echo "==> rsync dev → prod (зеркало, без data/, log/, venv/, .env)"
sudo rsync -a -h --info=stats1 --delete \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='*.log' \
  --exclude='/data/' \
  --exclude='/log/' \
  --exclude='/venv/' \
  --exclude='.env' \
  --exclude='.env.*' \
  "$SRC/" "$DST/"

# владелец prod-кода — appuser (rsync от root может оставить root:root)
sudo chown -R "$APP_USER:$APP_USER" "$DST"

# ---------- 4.1 smoke-test: import main до миграций и старта ----------
echo "==> smoke-test: import main"
if [[ -x "$PROD_VENV/bin/python" ]]; then
  if ! sudo -u "$APP_USER" "$PROD_VENV/bin/python" -c "import main" 2>&1; then
    echo "ОШИБКА: import main не прошёл после rsync. Бот остановлен." >&2
    echo "Откат: tar -xzf $CODE_ARCHIVE -C $DST" >&2
    exit 1
  fi
else
  echo "⚠️  venv не найден — smoke-test пропущен" >&2
fi

# ---------- 5. pip install -r requirements.txt ----------
if [[ -f "$DST/requirements.txt" ]]; then
  if [[ -x "$PROD_VENV/bin/pip" ]]; then
    echo "==> pip install -r requirements.txt в $PROD_VENV"
    sudo -u "$APP_USER" "$PROD_VENV/bin/pip" install --upgrade pip
    sudo -u "$APP_USER" "$PROD_VENV/bin/pip" install -r "$DST/requirements.txt"
  else
    echo "⚠️  Не найден venv ($PROD_VENV) — пропускаю pip install" >&2
  fi
else
  echo "Нет $DST/requirements.txt — пропуск pip install"
fi

# ---------- 6. миграции ----------
if [[ ${#NEW_MIGRATIONS[@]} -gt 0 ]]; then
  echo "==> новые миграции (${#NEW_MIGRATIONS[@]} шт.):"
  for rel in "${NEW_MIGRATIONS[@]}"; do
    echo "    - $rel"
  done
  for rel in "${NEW_MIGRATIONS[@]}"; do
    f="$DST/$rel"
    if [[ ! -f "$f" ]]; then
      echo "Файл миграции не появился после rsync: $f" >&2
      exit 1
    fi
    echo "==> psql ON_ERROR_STOP $DB_NAME ← $rel"
    sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME" -f "$f"
  done
else
  echo "Новых миграций нет."
fi

# ---------- 7. start ----------
echo "==> supervisorctl start $SUPERVISOR_NAME"
sudo supervisorctl start "$SUPERVISOR_NAME"
sudo supervisorctl status "$SUPERVISOR_NAME" || true

echo
echo "Готово."
echo "  Архив кода: $CODE_ARCHIVE"
echo "  Дамп БД:    $DB_ARCHIVE"

if [[ "${SKIP_GIT_PUSH}" != "1" ]]; then
  echo ""
  echo "==> [git] Обновление GitHub монорепозитория kostya..."
  KOSTYA_ROOT="$(cd "${SRC}/.." && pwd)"
  sudo -u "${APP_USER}" env KOSTYA_ROOT="${KOSTYA_ROOT}" GIT_REMOTE_URL="${GIT_REMOTE_URL:-git@github.com:bah677/kostya.git}" \
    bash "${KOSTYA_ROOT}/scripts/git_push_deploy.sh"
fi
