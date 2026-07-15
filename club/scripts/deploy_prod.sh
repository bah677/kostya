#!/usr/bin/env bash
#
# Деплой club: dev -> несколько prod-проектов на одном сервере.
#
# Запуск:
#   ./scripts/deploy_prod.sh
#
# По умолчанию:
# - катим только основной клуб (DEPLOY_TARGETS=club)
# - накат Насти — отдельный scripts/deploy_nastya_prod.sh
# - на каждом проекте делаем backup кода + backup БД
# - миграции запускаются на каждом накате (RUN_MIGRATIONS=1)
#
# Настройки:
#   DEPLOY_TARGETS=club,nastya   # comma-separated: club,nastya
#   RUN_MIGRATIONS=1             # по умолчанию 1
#   RUN_ATTRIBUTION_BACKFILL=0   # по умолчанию 0
#   SKIP_SUDO_REEXEC=1           # не перезапускать через sudo
#   DEPLOY_RUN_USER=appuser
#   SKIP_GIT_PUSH=1              # не пушить код в GitHub после деплоя
#   GIT_REMOTE_URL=https://github.com/bah677/kostya.git
#
# twin-тексты:
# - основной проект (`club`) берёт тексты из bot/texts
# - `nastya` после rsync получает полную замену bot/texts из twin_texts/nastya
#
# Разовые dev-only скрипты (не зеркалятся на prod):
# - scripts/send_angel_announcement.py — анонс «Стать ангелом», запуск из dev с prod .env
#
set -euo pipefail

if [[ "${SKIP_SUDO_REEXEC:-0}" != "1" && "$(id -u)" -ne 0 ]]; then
  echo "==> Нужны права root — перезапуск через sudo (введите пароль)..."
  exec sudo -E "$(command -v bash)" "${BASH_SOURCE[0]}" "$@"
fi

CLUB_DEV_ROOT="${CLUB_DEV_ROOT:-/home/appuser/dev/kostya/club}"
CLUB_CODE_SNAPSHOTS_DIR="${CLUB_CODE_SNAPSHOTS_DIR:-/home/appuser/old_bots/club_deploy_snapshots}"
CLUB_DB_DUMPS_DIR="${CLUB_DB_DUMPS_DIR:-/home/appuser/old_bots/club_db_dumps}"
TWIN_TEXTS_ROOT="${TWIN_TEXTS_ROOT:-${CLUB_DEV_ROOT}/twin_texts}"
DEPLOY_TARGETS="${DEPLOY_TARGETS:-club}"

RUN_USER="${DEPLOY_RUN_USER:-${SUDO_USER:-appuser}}"

SKIP_TAR_BACKUP="${SKIP_TAR_BACKUP:-0}"
SKIP_SUPERVISOR="${SKIP_SUPERVISOR:-0}"
SKIP_RSYNC="${SKIP_RSYNC:-0}"
SKIP_PGDUMP="${SKIP_PGDUMP:-0}"
RUN_MIGRATIONS="${RUN_MIGRATIONS:-1}"
RUN_ATTRIBUTION_BACKFILL="${RUN_ATTRIBUTION_BACKFILL:-0}"
SKIP_PIP="${SKIP_PIP:-0}"
SKIP_CLEAN_PYCACHE="${SKIP_CLEAN_PYCACHE:-0}"
AUTO_INIT_TWIN_TEXTS="${AUTO_INIT_TWIN_TEXTS:-1}"
SKIP_TWIN_TEXTS="${SKIP_TWIN_TEXTS:-0}"
SKIP_GIT_PUSH="${SKIP_GIT_PUSH:-0}"

SC_PROG=""
CURRENT_TARGET_NAME=""
CURRENT_PROD_ROOT=""
CURRENT_TWIN_TEXTS_SUBDIR=""
_DEPLOY_FAILED_AFTER_STOP=0

declare -a TARGET_SUMMARY=()

die() { echo "ERROR: $*" >&2; exit 1; }

run_supervisorctl() {
  command -v supervisorctl >/dev/null || die "нужен supervisorctl"
  supervisorctl "$@"
}

supervisor_stop() {
  if [[ "${SKIP_SUPERVISOR}" == "1" ]]; then
    echo "==> SKIP_SUPERVISOR=1 — пропуск stop"
    return 0
  fi
  echo "==> supervisorctl stop ${SC_PROG}"
  if run_supervisorctl stop "${SC_PROG}"; then
    _DEPLOY_FAILED_AFTER_STOP=1
  else
    echo "WARN: stop не выполнен. Проверьте: supervisorctl status ${SC_PROG}" >&2
  fi
}

supervisor_start() {
  if [[ "${SKIP_SUPERVISOR}" == "1" ]]; then
    echo "==> SKIP_SUPERVISOR=1 — пропуск start"
    return 0
  fi
  echo "==> supervisorctl start ${SC_PROG}"
  run_supervisorctl start "${SC_PROG}"
  run_supervisorctl status "${SC_PROG}" || true
  _DEPLOY_FAILED_AFTER_STOP=0
}

chown_to_run_user_if_root() {
  local f="$1"
  [[ "$(id -u)" -eq 0 && -n "${f}" && -e "${f}" ]] || return 0
  chown "${RUN_USER}:${RUN_USER}" "${f}"
}

pick_writable_dump_dir() {
  local preferred="${CLUB_DB_DUMPS_DIR}"
  local fallback="/home/${RUN_USER}/old_bots/club_db_dumps"
  if [[ "$(id -u)" -eq 0 ]]; then
    mkdir -p "${preferred}"
    echo "${preferred}"
    return 0
  fi
  mkdir -p "${preferred}" 2>/dev/null || true
  if [[ -w "${preferred}" ]] && touch "${preferred}/.deploy_w_chk_$$" 2>/dev/null; then
    rm -f "${preferred}/.deploy_w_chk_$$"
    echo "${preferred}"
    return 0
  fi
  mkdir -p "${fallback}"
  echo "WARN: нет записи в ${preferred} — дамп в ${fallback}" >&2
  echo "${fallback}"
}

on_err() {
  local ec=$?
  if [[ "${_DEPLOY_FAILED_AFTER_STOP}" == "1" ]]; then
    echo "" >&2
    echo "!!! Ошибка деплоя target=${CURRENT_TARGET_NAME}, код ${ec}. Процесс мог остаться ОСТАНОВЛЕННЫМ." >&2
    echo "    Проверьте: supervisorctl status ${SC_PROG}" >&2
    echo "    Поднять:   supervisorctl start ${SC_PROG}" >&2
  fi
  exit "${ec}"
}
trap on_err ERR

resolve_target() {
  local target="$1"
  case "$target" in
    club)
      CURRENT_TARGET_NAME="club"
      CURRENT_PROD_ROOT="/home/appuser/club"
      SC_PROG="bots:club_bot"
      CURRENT_TWIN_TEXTS_SUBDIR=""
      ;;
    nastya|club_nastya)
      CURRENT_TARGET_NAME="nastya"
      CURRENT_PROD_ROOT="/home/appuser/club_nastya"
      SC_PROG="club_nastya:club_nastya_bot"
      CURRENT_TWIN_TEXTS_SUBDIR="nastya"
      ;;
    *)
      die "Неизвестный target '${target}'. Разрешены: club,nastya"
      ;;
  esac
}

validate_common_requirements() {
  [[ -d "${CLUB_DEV_ROOT}" ]] || die "Нет каталога dev: ${CLUB_DEV_ROOT}"
  if [[ "$(id -u)" -eq 0 ]]; then
    getent passwd "${RUN_USER}" >/dev/null || die "Нет локального пользователя ${RUN_USER} (задайте DEPLOY_RUN_USER)"
  fi
  command -v rsync >/dev/null || die "нужен rsync"
  command -v tar >/dev/null || die "нужен tar"
}

validate_target_requirements() {
  [[ -d "${CURRENT_PROD_ROOT}" ]] || die "Нет каталога prod: ${CURRENT_PROD_ROOT}"
  [[ -f "${CURRENT_PROD_ROOT}/.env" ]] || die "Нет ${CURRENT_PROD_ROOT}/.env"
  if [[ -n "${CURRENT_TWIN_TEXTS_SUBDIR}" ]]; then
    local twin_dir="${TWIN_TEXTS_ROOT}/${CURRENT_TWIN_TEXTS_SUBDIR}"
    if [[ ! -d "${twin_dir}" ]]; then
      if [[ "${AUTO_INIT_TWIN_TEXTS}" == "1" ]]; then
        echo "==> twin-тексты не найдены, bootstrap из bot/texts: ${twin_dir}"
        mkdir -p "${twin_dir}"
        rsync -a --delete "${CLUB_DEV_ROOT}/bot/texts/" "${twin_dir}/"
      else
        die "Нет twin-текстов: ${twin_dir}"
      fi
    fi
  fi
}

apply_twin_texts_if_needed() {
  if [[ "${SKIP_TWIN_TEXTS}" == "1" ]]; then
    echo "==>     twin texts overlay skipped (SKIP_TWIN_TEXTS=1)"
    return 0
  fi
    if [[ -z "${CURRENT_TWIN_TEXTS_SUBDIR}" ]]; then
        return 0
    fi
    local twin_dir="${TWIN_TEXTS_ROOT}/${CURRENT_TWIN_TEXTS_SUBDIR}"
    local prod_texts="${CURRENT_PROD_ROOT}/bot/texts"
    echo "==>     проверка twin-текстов..."
    if ! python3 "${CLUB_DEV_ROOT}/scripts/check_nastya_twin_texts.py"; then
        die "Добавьте недостающие файлы в ${twin_dir}/"
    fi
    echo "==>     twin texts overlay: ${twin_dir}/ -> ${prod_texts}/"
  mkdir -p "${prod_texts}"
  rsync -a --delete "${twin_dir}/" "${prod_texts}/"
}

sync_aboutclub_files() {
  local root_file="${CURRENT_PROD_ROOT}/aboutclub.txt"
  local texts_file="${CURRENT_PROD_ROOT}/bot/texts/aboutclub.txt"
  mkdir -p "$(dirname "${texts_file}")"
  if [[ -f "${texts_file}" ]]; then
    cp "${texts_file}" "${root_file}"
    echo "==>     synced aboutclub: ${texts_file} -> ${root_file}"
    return 0
  fi
  if [[ -f "${root_file}" ]]; then
    cp "${root_file}" "${texts_file}"
    echo "==>     synced aboutclub: ${root_file} -> ${texts_file}"
    return 0
  fi
  echo "WARN: aboutclub source file not found in ${CURRENT_PROD_ROOT}" >&2
}

deploy_target() {
  local ts="$1"
  local archive=""
  local dump_file=""
  local prod_name
  local prod_parent

  prod_name="$(basename "${CURRENT_PROD_ROOT}")"
  prod_parent="$(dirname "${CURRENT_PROD_ROOT}")"

  echo ""
  echo "============================================================"
  echo "==> DEPLOY TARGET: ${CURRENT_TARGET_NAME}"
  echo "    prod_root: ${CURRENT_PROD_ROOT}"
  echo "    supervisor: ${SC_PROG}"
  echo "    migrations: RUN_MIGRATIONS=${RUN_MIGRATIONS}"
  echo "============================================================"

  if [[ "${SKIP_TAR_BACKUP}" != "1" ]]; then
    mkdir -p "${CLUB_CODE_SNAPSHOTS_DIR}" || die "нет доступа на запись в ${CLUB_CODE_SNAPSHOTS_DIR}"
    archive="${CLUB_CODE_SNAPSHOTS_DIR}/club_code_${CURRENT_TARGET_NAME}_${ts}.tgz"
    echo "==> [1/7] Архив кода прода: ${archive}"
    (
      cd "${prod_parent}"
      tar -czf "${archive}" \
        --exclude="${prod_name}/venv" \
        --exclude="${prod_name}/.venv" \
        --exclude="${prod_name}/data" \
        --exclude="${prod_name}/exports" \
        --exclude='__pycache__' \
        --exclude="${prod_name}/.mypy_cache" \
        --exclude="${prod_name}/.pytest_cache" \
        --exclude="${prod_name}/.ruff_cache" \
        --exclude='*.pyc' \
        "${prod_name}"
    )
    chown_to_run_user_if_root "${archive}"
    echo "    $(du -h "${archive}" | cut -f1)"
  else
    echo "==> [1/7] SKIP_TAR_BACKUP=1"
  fi

  echo "==> [2/7] Остановка Supervisor (${SC_PROG})"
  supervisor_stop

  if [[ "${SKIP_RSYNC}" != "1" ]]; then
    echo "==> [3/7] rsync ${CLUB_DEV_ROOT}/ -> ${CURRENT_PROD_ROOT}/"
    rsync -a --delete \
      --filter='protect venv/' \
      --filter='protect .venv/' \
      --filter='protect .env' \
      --filter='protect data/' \
      --filter='protect log/' \
      --filter='protect LogFromProd/' \
      --filter='protect exports/' \
      --exclude='venv/' \
      --exclude='.venv/' \
      --exclude='.env' \
      --exclude='data/' \
      --exclude='log/' \
      --exclude='LogFromProd/' \
      --exclude='exports/' \
      --exclude='scripts/send_angel_announcement.py' \
      --exclude='scripts/send_angel_pool_replay_notifications.py' \
      "${CLUB_DEV_ROOT}/" "${CURRENT_PROD_ROOT}/"
    apply_twin_texts_if_needed
    sync_aboutclub_files
    echo "    rsync: сохранены .env venv/.venv data/ log/ LogFromProd/ exports/"
    if [[ "$(id -u)" -eq 0 ]]; then
      echo "    chown -R ${RUN_USER} -> ${CURRENT_PROD_ROOT}"
      chown -R "${RUN_USER}:${RUN_USER}" "${CURRENT_PROD_ROOT}"
    fi
    if [[ "${SKIP_CLEAN_PYCACHE}" != "1" ]]; then
      echo "    очистка __pycache__ (кроме prod venv/.venv) ..."
      while IFS= read -r -d '' d; do rm -rf "$d"; done < <(
        find "${CURRENT_PROD_ROOT}" \
          -path "${CURRENT_PROD_ROOT}/venv/*" -prune -o \
          -path "${CURRENT_PROD_ROOT}/.venv/*" -prune -o \
          -type d -name '__pycache__' -print0 2>/dev/null
      )
    fi
  else
    echo "==> [3/7] SKIP_RSYNC=1"
  fi

  if [[ "${SKIP_PGDUMP}" != "1" ]]; then
    local dump_dir
    dump_dir="$(pick_writable_dump_dir)"
    dump_file="${dump_dir}/club_db_${CURRENT_TARGET_NAME}_${ts}.dump"
    echo "==> [4/7] pg_dump -Fc -> ${dump_file}"
    command -v pg_dump >/dev/null || die "нужен pg_dump"
    set -a
    # shellcheck disable=SC1090
    source "${CURRENT_PROD_ROOT}/.env"
    set +a
    : "${DB_NAME:?В .env нужен DB_NAME}"
    : "${DB_USER:?В .env нужен DB_USER}"
    : "${DB_PASSWORD:?В .env нужен DB_PASSWORD}"
    DB_HOST="${DB_HOST:-localhost}"
    DB_PORT="${DB_PORT:-5432}"
    export PGPASSWORD="${DB_PASSWORD}"
    pg_dump -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" -Fc -f "${dump_file}"
    unset PGPASSWORD
    chown_to_run_user_if_root "${dump_file}"
    echo "    $(du -h "${dump_file}" | cut -f1)"
  else
    echo "==> [4/7] SKIP_PGDUMP=1"
  fi

  if [[ "${SKIP_PIP}" != "1" && -f "${CURRENT_PROD_ROOT}/requirements.txt" ]]; then
    echo "==> [5/7] pip install (venv прода, пользователь ${RUN_USER})"
    if [[ -x "${CURRENT_PROD_ROOT}/venv/bin/pip" ]]; then
      if [[ "$(id -u)" -eq 0 ]]; then
        sudo -u "${RUN_USER}" -- "${CURRENT_PROD_ROOT}/venv/bin/pip" install -q -r "${CURRENT_PROD_ROOT}/requirements.txt"
      else
        "${CURRENT_PROD_ROOT}/venv/bin/pip" install -q -r "${CURRENT_PROD_ROOT}/requirements.txt"
      fi
    elif [[ -x "${CURRENT_PROD_ROOT}/.venv/bin/pip" ]]; then
      if [[ "$(id -u)" -eq 0 ]]; then
        sudo -u "${RUN_USER}" -- "${CURRENT_PROD_ROOT}/.venv/bin/pip" install -q -r "${CURRENT_PROD_ROOT}/requirements.txt"
      else
        "${CURRENT_PROD_ROOT}/.venv/bin/pip" install -q -r "${CURRENT_PROD_ROOT}/requirements.txt"
      fi
    else
      die "Нет ${CURRENT_PROD_ROOT}/venv/bin/pip — создайте venv на проде или SKIP_PIP=1"
    fi
  else
    echo "==> [5/7] SKIP_PIP=1 или нет requirements.txt"
  fi

  if [[ "${RUN_MIGRATIONS}" == "1" ]]; then
    command -v psql >/dev/null || die "для apply_all_db_migrations.sh нужен psql"
    echo "==> [6/7] apply_all_db_migrations.sh (пользователь ${RUN_USER})"
    local -a mig_env=()
    if [[ "${RUN_ATTRIBUTION_BACKFILL}" != "1" ]]; then
      mig_env+=(RUN_ATTRIBUTION_BACKFILL=0)
    fi
    if [[ "$(id -u)" -eq 0 ]]; then
      sudo -u "${RUN_USER}" -- env HOME="$(getent passwd "${RUN_USER}" | cut -d: -f6)" \
        "${mig_env[@]}" bash "${CURRENT_PROD_ROOT}/scripts/apply_all_db_migrations.sh"
    else
      env "${mig_env[@]}" bash "${CURRENT_PROD_ROOT}/scripts/apply_all_db_migrations.sh"
    fi
  else
    echo "==> [6/7] миграции пропущены (RUN_MIGRATIONS=0)"
  fi

  echo "==> [7/7] Запуск Supervisor (${SC_PROG})"
  supervisor_start

  TARGET_SUMMARY+=("target=${CURRENT_TARGET_NAME} archive=${archive:-n/a} dump=${dump_file:-n/a}")
}

main() {
  local ts
  ts="$(date +%Y%m%d_%H%M%S)"
  validate_common_requirements
  mkdir -p "${CLUB_CODE_SNAPSHOTS_DIR}" 2>/dev/null || true

  IFS=',' read -r -a targets <<< "${DEPLOY_TARGETS}"
  [[ "${#targets[@]}" -gt 0 ]] || die "DEPLOY_TARGETS пуст"

  for target in "${targets[@]}"; do
    target="${target// /}"
    [[ -n "${target}" ]] || continue
    resolve_target "${target}"
    validate_target_requirements
    deploy_target "${ts}"
  done
}

main "$@"

trap - ERR
_DEPLOY_FAILED_AFTER_STOP=0

echo ""
echo "==> Готово. Сводка:"
for line in "${TARGET_SUMMARY[@]}"; do
  echo "    ${line}"
done

if [[ "${SKIP_GIT_PUSH}" != "1" ]]; then
  echo ""
  echo "==> [git] Обновление GitHub монорепозитория kostya..."
  KOSTYA_ROOT="$(cd "${CLUB_DEV_ROOT}/.." && pwd)"
  sudo -u "${RUN_USER}" env KOSTYA_ROOT="${KOSTYA_ROOT}" GIT_REMOTE_URL="${GIT_REMOTE_URL:-git@github.com:bah677/kostya.git}" \
    bash "${KOSTYA_ROOT}/scripts/git_push_deploy.sh"
fi
