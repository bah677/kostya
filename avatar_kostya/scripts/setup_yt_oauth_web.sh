#!/usr/bin/env bash
# Статический сайт OAuth (privacy / terms) + nginx + Let's Encrypt.
#
# 1) Пропишите DNS (см. вывод скрипта).
# 2) Запуск:
#      ./scripts/setup_yt_oauth_web.sh yt-oauth.ВАШ-ДОМЕН.ru
#
# Требует sudo для nginx и certbot.

set -euo pipefail

AVATAR_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOMAIN="${1:-}"
SERVER_IP="${YT_OAUTH_SERVER_IP:-144.124.239.159}"

if [[ -z "${DOMAIN}" ]]; then
  echo "Usage: $0 <domain>" >&2
  echo "Example: $0 yt-oauth.example.com" >&2
  exit 1
fi

if [[ "${DOMAIN}" == *"github.io"* ]] || [[ "${DOMAIN}" == *"google.com"* ]]; then
  echo "Use your own domain (not github.io / google.com)." >&2
  exit 1
fi

WEB_ROOT="${AVATAR_ROOT}/www/yt-oauth"
TEMPLATE="${AVATAR_ROOT}/deploy/nginx/yt-oauth-pages.http.conf"
SITE_AVAILABLE="/etc/nginx/sites-available/yt-oauth-pages"
SITE_ENABLED="/etc/nginx/sites-enabled/yt-oauth-pages"

echo "=== DNS (у регистратора домена) ==="
echo ""
echo "Тип:  A"
echo "Имя:  ${DOMAIN%%.*}  (если полное имя ${DOMAIN} — см. ниже)"
echo "      Для поддомена yt-oauth.example.com → имя/host: yt-oauth"
echo "      Для корня example.com → имя/host: @"
echo "Значение: ${SERVER_IP}"
echo "TTL: 300 (или по умолчанию)"
echo ""
echo "Проверка после пропagation:"
echo "  dig +short ${DOMAIN} A"
echo "  curl -I http://${DOMAIN}/"
echo ""

if ! [[ -d "${WEB_ROOT}" ]]; then
  echo "Нет ${WEB_ROOT}" >&2
  exit 1
fi

TMP_CONF="$(mktemp)"
sed "s/DOMAIN/${DOMAIN}/g" "${TEMPLATE}" > "${TMP_CONF}"

echo "==> nginx site ${DOMAIN}"
sudo cp "${TMP_CONF}" "${SITE_AVAILABLE}"
rm -f "${TMP_CONF}"
sudo ln -sf "${SITE_AVAILABLE}" "${SITE_ENABLED}"
sudo nginx -t
sudo systemctl reload nginx

echo "==> HTTP ok (проверьте: http://${DOMAIN}/)"

if command -v certbot >/dev/null 2>&1; then
  echo "==> certbot (HTTPS)"
  sudo certbot --nginx -d "${DOMAIN}" --non-interactive --agree-tos -m baharev02@gmail.com || {
    echo "certbot failed — DNS ещё не указывает на сервер или порт 80 закрыт." >&2
    echo "Повторите позже: sudo certbot --nginx -d ${DOMAIN}" >&2
  }
else
  echo "certbot не найден. HTTPS: sudo apt install certbot python3-certbot-nginx" >&2
fi

BASE_DOMAIN="${DOMAIN}"
if [[ "${DOMAIN}" =~ ^[^.]+\.[^.]+\..+ ]]; then
  BASE_DOMAIN="${DOMAIN#*.}"
fi

echo ""
echo "=== Google Cloud OAuth consent screen ==="
echo ""
echo "Application home page:"
echo "  https://${DOMAIN}/"
echo "Privacy policy:"
echo "  https://${DOMAIN}/privacy.html"
echo "Terms of service:"
echo "  https://${DOMAIN}/terms.html"
echo "Authorized domains (только корень, без https):"
echo "  ${BASE_DOMAIN}"
echo ""
echo "Test users: baharev02@gmail.com"
echo ""
