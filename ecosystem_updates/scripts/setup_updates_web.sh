#!/usr/bin/env bash
# Статический сайт updates.mironbot.ru + nginx + Let's Encrypt.
#
# 1) Пропишите DNS (см. вывод).
# 2) Запуск:
#      ./scripts/setup_updates_web.sh updates.mironbot.ru
#
# Требует sudo для nginx и certbot.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOMAIN="${1:-updates.mironbot.ru}"
SERVER_IP="${UPDATES_SERVER_IP:-144.124.239.159}"

WEB_ROOT="${ROOT}/site"
TEMPLATE="${ROOT}/nginx/updates.mironbot.ru.http.conf"
SITE_AVAILABLE="/etc/nginx/sites-available/updates-mironbot"
SITE_ENABLED="/etc/nginx/sites-enabled/updates-mironbot"

echo "=== DNS (у регистратора домена mironbot.ru) ==="
echo ""
echo "Тип записи:   A"
echo "Имя / Host:   updates"
echo "Значение:     ${SERVER_IP}"
echo "TTL:          300 (или Autо)"
echo ""
echo "Итоговый хост: ${DOMAIN} → ${SERVER_IP}"
echo ""
echo "Проверка после распространения DNS:"
echo "  dig +short ${DOMAIN} A"
echo "  # должно быть: ${SERVER_IP}"
echo ""

bash "${ROOT}/scripts/publish.sh"

TMP_CONF="$(mktemp)"
sed "s/DOMAIN/${DOMAIN}/g" "${TEMPLATE}" > "${TMP_CONF}"

echo "==> nginx site ${DOMAIN}"
sudo cp "${TMP_CONF}" "${SITE_AVAILABLE}"
rm -f "${TMP_CONF}"
sudo ln -sf "${SITE_AVAILABLE}" "${SITE_ENABLED}"
sudo nginx -t
sudo systemctl reload nginx

echo "==> HTTP: http://${DOMAIN}/"

if command -v certbot >/dev/null 2>&1; then
  echo "==> certbot (HTTPS)"
  sudo certbot --nginx -d "${DOMAIN}" --non-interactive --agree-tos -m baharev02@gmail.com || {
    echo "certbot failed — DNS ещё не на сервер или порт 80 закрыт." >&2
    echo "Позже: sudo certbot --nginx -d ${DOMAIN}" >&2
  }
else
  echo "certbot не найден. HTTPS: sudo apt install certbot python3-certbot-nginx" >&2
fi

echo ""
echo "Готово (после DNS + certbot): https://${DOMAIN}/"
