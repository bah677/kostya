#!/usr/bin/env bash
# Обновляет пути avatar_kostya в /etc/supervisor/conf.d/bots.conf
# и перечитывает supervisor. Запускать вручную с sudo:
#
#   sudo bash /home/appuser/dev/kostya/scripts/supervisor_avatar_kostya_paths.sh
#
set -euo pipefail

CONF=/etc/supervisor/conf.d/bots.conf
OLD=/home/appuser/dev/avatar_kostya
NEW=/home/appuser/dev/kostya/avatar_kostya

[[ -f "${CONF}" ]] || { echo "нет ${CONF}"; exit 1; }
[[ -d "${NEW}" ]] || { echo "нет ${NEW}"; exit 1; }

cp -a "${CONF}" "${CONF}.bak.$(date +%Y%m%d_%H%M%S)"
sed -i "s|${OLD}|${NEW}|g" "${CONF}"

echo "==> обновлено в ${CONF}:"
grep -n 'avatar_kostya' "${CONF}" | head -20

echo "==> supervisorctl reread && update"
supervisorctl reread
supervisorctl update

echo "==> статус avatar_kostya:"
supervisorctl status avatar_kostya || supervisorctl status bots:avatar_kostya || true

echo "Готово. При необходимости: supervisorctl restart avatar_kostya"
