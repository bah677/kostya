#!/usr/bin/env bash
#
# Дописывает испанского бота «Habla con Dios» в конец bots.conf и поднимает
# только его.
#
# Тот же код, что у biblia_bot, отличается одной переменной BOT_ENV_FILE:
# свой токен, своя база, BOT_LANG=es.
#
# Запуск (нужен root — пишем в /etc/supervisor/conf.d):
#   sudo bash /home/appuser/biblia/scripts/install_es_supervisor.sh
#
set -euo pipefail

# Без этого молчаливый выход выглядит как «скрипт ничего не сделал». Так и
# случилось в первый раз: grep не нашёл ключа, pipefail убил скрипт, а на
# экране не появилось ни строчки.
trap 'rc=$?; [[ $rc -ne 0 ]] && echo "ОБОРВАЛОСЬ на строке ${LINENO}, код ${rc}" >&2; exit $rc' ERR

ROOT="${BIBLIA_ROOT:-/home/appuser/biblia}"
ENV_ES="${ROOT}/.env.es"
CONF="${SUPERVISOR_CONF:-/etc/supervisor/conf.d/bots.conf}"
PROGRAM="biblia_es"
RUN_USER="${BIBLIA_RUN_USER:-appuser}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Нужны права root: sudo bash $0" >&2
  exit 1
fi

for f in "${CONF}" "${ENV_ES}" "${ROOT}/.env"; do
  [[ -f "$f" ]] || { echo "Нет файла: $f" >&2; exit 1; }
done
[[ -x "${ROOT}/venv/bin/python3" ]] || {
  echo "Нет ${ROOT}/venv/bin/python3 — неверный каталог бота." >&2; exit 1; }

# ── проверки до записи ──────────────────────────────────────────────────────
# Иначе supervisor поднимет бота, который сразу упадёт, и это будет выглядеть
# как поломка кода.
if grep -qE '^BIBLIA_BOT_TOKEN=(ПОДСТАВЬТЕ_ТОКЕН)?[[:space:]]*$' "${ENV_ES}"; then
  echo "В ${ENV_ES} не вписан BIBLIA_BOT_TOKEN." >&2
  exit 1
fi

# dotenv берёт ПОСЛЕДНЕЕ значение ключа — сверяем именно последние.
# "|| true" обязательно: без него отсутствие ключа валит скрипт молча.
last_val() {
  grep -E "^$1=" "$2" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '[:space:]' || true
}

# Имя базы русского бота: в его .env может стоять только общий DB_NAME —
# config.py подхватывает его как запасной (см. _biblia_db_name).
ru_db_name() {
  local v
  v=$(last_val BIBLIA_DB_NAME "${ROOT}/.env")
  [[ -n "$v" ]] || v=$(last_val DB_NAME "${ROOT}/.env")
  printf '%s' "$v"
}

es_lang=$(last_val BOT_LANG "${ENV_ES}")
es_db=$(last_val BIBLIA_DB_NAME "${ENV_ES}")
ru_db=$(ru_db_name)
es_token=$(last_val BIBLIA_BOT_TOKEN "${ENV_ES}")
ru_token=$(last_val BIBLIA_BOT_TOKEN "${ROOT}/.env")

if [[ -z "${ru_db}" ]]; then
  echo "Не нашёл имя базы русского бота в ${ROOT}/.env (ни BIBLIA_DB_NAME, ни DB_NAME)." >&2
  exit 1
fi

[[ "${es_lang}" == "es" ]] || {
  echo "В ${ENV_ES} BOT_LANG=${es_lang:-пусто}, ожидали es." >&2; exit 1; }
[[ -n "${es_db}" && "${es_db}" != "${ru_db}" ]] || {
  echo "База ${es_db:-пусто} совпадает с русской (${ru_db})." >&2
  echo "Два бота в одной базе перемешают пользователей и переписку." >&2
  exit 1; }
[[ -n "${es_token}" && "${es_token}" != "${ru_token}" ]] || {
  echo "Токен в ${ENV_ES} совпадает с токеном русского бота." >&2
  echo "Два процесса на одном токене будут отбирать апдейты друг у друга." >&2
  exit 1; }

echo "==> каталог ${ROOT}"
echo "==> env     ${ENV_ES}  (язык ${es_lang}, база ${es_db}, у русского ${ru_db})"
echo "==> конфиг  ${CONF}"

if grep -q "^\[program:${PROGRAM}\]" "${CONF}"; then
  echo "==> блок [program:${PROGRAM}] в конфиге уже есть — не дублирую"
else
  BAK="${CONF}.bak.$(date +%Y%m%d_%H%M%S)"
  cp -a "${CONF}" "${BAK}"
  echo "==> копия прежнего конфига: ${BAK}"

  cat >> "${CONF}" <<EOF

; Испанский бот «Habla con Dios». Тот же код, что у biblia_bot, отличается
; только BOT_ENV_FILE: свой токен, своя база, BOT_LANG=es.
; Намеренно вне [group:bots]: перезапуск группы его не трогает, и наоборот.
[program:${PROGRAM}]
command=${ROOT}/venv/bin/python3 ${ROOT}/main.py
directory=${ROOT}
user=${RUN_USER}
autostart=true
autorestart=true
startsecs=10
startretries=3
stopwaitsecs=30
stopasgroup=true
killasgroup=true
stderr_logfile=/var/log/supervisor/${PROGRAM}.err.log
stdout_logfile=/var/log/supervisor/${PROGRAM}.out.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=5
environment=PYTHONUNBUFFERED=1,PYTHONPATH="${ROOT}",PATH="${ROOT}/venv/bin:%(ENV_PATH)s",BOT_ENV_FILE="${ENV_ES}"
EOF
  echo "==> блок дописан в конец ${CONF}"
fi

# reread перечитывает конфиги, update с именем программы поднимает только её —
# работающие боты не перезапускаются.
supervisorctl reread
supervisorctl update "${PROGRAM}"

sleep 4
supervisorctl status "${PROGRAM}" || true

cat <<EOF

Логи:
  tail -f /var/log/supervisor/${PROGRAM}.out.log
  tail -f /var/log/supervisor/${PROGRAM}.err.log

Перезапуск после правок кода:
  sudo supervisorctl restart ${PROGRAM}

Русский бот не затронут: он остался в [group:bots], его не перезапускали.
EOF
