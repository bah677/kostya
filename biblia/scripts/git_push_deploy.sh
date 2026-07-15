#!/usr/bin/env bash
# Обёртка: push идёт из монорепозитория /home/appuser/dev/kostya → bah677/kostya
set -euo pipefail
KOSTYA_ROOT="${KOSTYA_ROOT:-/home/appuser/dev/kostya}"
exec bash "${KOSTYA_ROOT}/scripts/git_push_deploy.sh" "$@"
