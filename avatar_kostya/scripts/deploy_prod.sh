#!/usr/bin/env bash
#
# Упрощённый «деплой» avatar_kostya: процесс уже из dev, rsync не нужен.
#
#   1) ротация: backups/data 7д + архивы логов 30д
#   2) sudo supervisorctl restart avatar:avatar_kostya
#   3) git commit + push монорепо kostya (как club/biblia)
#
# Запуск:
#   ./scripts/deploy_prod.sh
#   SKIP_GIT_PUSH=1 ./scripts/deploy_prod.sh
#   SKIP_RETENTION=1 ./scripts/deploy_prod.sh
#
set -euo pipefail

AVATAR_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KOSTYA_ROOT="$(cd "${AVATAR_ROOT}/.." && pwd)"
SUPERVISOR_NAME="${SUPERVISOR_NAME:-avatar:avatar_kostya}"
SKIP_GIT_PUSH="${SKIP_GIT_PUSH:-0}"
SKIP_RETENTION="${SKIP_RETENTION:-0}"
SKIP_RESTART="${SKIP_RESTART:-0}"
GIT_REMOTE_URL="${GIT_REMOTE_URL:-git@github.com:bah677/kostya.git}"
RETENTION_SH="${KOSTYA_ROOT}/scripts/disk_retention.sh"
RUN_USER="${DEPLOY_RUN_USER:-${SUDO_USER:-appuser}}"

echo "avatar_kostya deploy (restart-only)"
echo "  root: ${AVATAR_ROOT}"
echo "  supervisor: ${SUPERVISOR_NAME}"

if [[ "${SKIP_RETENTION}" != "1" ]]; then
  if [[ -x "${RETENTION_SH}" ]]; then
    echo "==> disk retention (backups/data 7d, log arc 30d)"
    BACKUP_DAYS=7 DATA_DAYS=7 LOG_ARC_DAYS=30 \
      "${RETENTION_SH}" deploy --apply || true
  else
    echo "WARN: нет ${RETENTION_SH} — пропуск retention" >&2
  fi
fi

if [[ "${SKIP_RESTART}" != "1" ]]; then
  echo "==> supervisorctl restart ${SUPERVISOR_NAME}"
  sudo supervisorctl restart "${SUPERVISOR_NAME}"
  sudo supervisorctl status "${SUPERVISOR_NAME}" || true
else
  echo "==> SKIP_RESTART=1"
fi

if [[ "${SKIP_GIT_PUSH}" != "1" ]]; then
  echo ""
  echo "==> [git] Обновление GitHub монорепозитория kostya..."
  if [[ "$(id -u)" -eq 0 ]]; then
    sudo -u "${RUN_USER}" env KOSTYA_ROOT="${KOSTYA_ROOT}" GIT_REMOTE_URL="${GIT_REMOTE_URL}" \
      bash "${KOSTYA_ROOT}/scripts/git_push_deploy.sh"
  else
    env KOSTYA_ROOT="${KOSTYA_ROOT}" GIT_REMOTE_URL="${GIT_REMOTE_URL}" \
      bash "${KOSTYA_ROOT}/scripts/git_push_deploy.sh"
  fi
fi

echo "Готово."
