"""Тексты (ES) интерфейса донатов.

Испанский нейтральный латиноамериканский: обращение на «tú», без «vosotros»
и кастильских оборотов. Основная аудитория — Мексика, Колумбия, Перу.

Рублёвая и евро-кнопки оставлены на случай, если язык когда-нибудь откроют
на Европу; сейчас профиль языка отдаёт только USD, и в меню они не попадают.
"""

from __future__ import annotations

# ── валюты ──────────────────────────────────────────────────────────────────
CURRENCY_BTN = {
    "RUB": "🇷🇺 Rublos",
    "USD": "💵 Dólares",
    "EUR": "💶 Euros",
}

# ── вступление ──────────────────────────────────────────────────────────────
INTRO = (
    "🤝 **Apoya este proyecto**\n\n"
    "Tu aporte ayuda a que siga siendo gratuito para quien lo necesita.\n"
    "Es una siembra en algo bueno 🙏🏻\n\n"
)

# ── кнопки ──────────────────────────────────────────────────────────────────
BTN_MONTHLY = "📅 Apoyo mensual"
BTN_ONE_TIME = "💳 Aporte único"
BTN_MY_SUBSCRIPTION = "📋 Mi suscripción"
BTN_CANCEL = "❌ Cancelar"
BTN_CRYPTO = "₿ Criptomoneda"
BTN_BACK = "◀️ Atrás"
BTN_OTHER_AMOUNT = "✏️ Otro monto"
BTN_CLOSE = "❌ Cerrar"
BTN_PAY = "🔗 Pagar"
BTN_CANCEL_SUBSCRIPTION = "❌ Cancelar suscripción"
BTN_CONFIRM_CANCEL = "✅ Sí, cancelar"

# ── шаги ────────────────────────────────────────────────────────────────────
CHOOSE_CURRENCY = "Elige la moneda:"
CHOOSE_MODE = "Elige cómo quieres apoyar:"
TITLE_MONTHLY = "📅 **Apoyo mensual**\n\nElige la moneda:"
TITLE_ONE_TIME = "💳 **Aporte único**\n\nElige la moneda:"
ANY_AMOUNT = "💎 **Cualquier monto suma**"
ENTER_AMOUNT = "💎 **Escribe el monto en {cur} (mínimo {min_amt} {sym}):**"
AMOUNT_TOO_SMALL = (
    "❌ El mínimo es {min_amt} {sym}. Escribe {min_amt} o más."
)
AMOUNT_NOT_A_NUMBER = "❌ Escribe un monto válido, solo números."

# ── создание платежа ────────────────────────────────────────────────────────
CREATING_PAYMENT = "⏳ Creando el pago..."
PAYMENT_FAILED = "❌ No se pudo crear el pago. Inténtalo más tarde."
PAY_LINK_SUBSCRIPTION = "Para activar la suscripción, entra al enlace de abajo:"
PAY_LINK_ONE_TIME = "Para pagar, entra al enlace de abajo:"
SUBSCRIPTION_TITLE = "Apoyo mensual {amount} {cur}"
SUMMARY_MONTHLY = "📅 **Apoyo mensual:** {amount} {cur} / mes\n\n"
SUMMARY_ONE_TIME = "💰 **Monto:** {amount} {cur}\n\n"

# ── подписка ────────────────────────────────────────────────────────────────
MY_SUBSCRIPTION = "📋 **Tu suscripción**\n\n"
SUBSCRIPTION_AMOUNT = "💰 {amount} {sym} / mes\n"
SUBSCRIPTION_STATUS = "📌 Estado: {status}"
NEXT_CHARGE = "\n📆 Próximo cobro: {when}"
NO_ACTIVE_SUBSCRIPTION = "No encontré una suscripción activa"
CONFIRM_CANCEL = (
    "¿Seguro que quieres cancelar el apoyo mensual?\n"
    "No se harán más cobros."
)
SUBSCRIPTION_NOT_FOUND = "No encontré la suscripción"
SERVICE_UNAVAILABLE = "El servicio no está disponible"
CANCEL_FAILED = (
    "❌ No se pudo cancelar la suscripción. Inténtalo más tarde o escríbenos."
)
CANCELED_FULL = "✅ Suscripción cancelada. ¡Gracias por haber apoyado el proyecto!"
CANCELED_TOAST = "Suscripción cancelada"
MONTHLY_UNAVAILABLE = "El apoyo mensual no está disponible por ahora"

# ── крипта ──────────────────────────────────────────────────────────────────
CRYPTO_TITLE = "💎 **Aporte en cripto**\n\n"
CRYPTO_LEAD = "Puedes apoyar el proyecto enviando fondos a esta dirección:\n\n"
CRYPTO_NETWORK = "📌 **Red:** TRC-20 (Tron)\n"
CRYPTO_WARNING = (
    "💡 **Importante:** asegúrate de usar la red correcta al enviar.\n\n"
)
CRYPTO_THANKS = "¡Gracias por tu apoyo! ❤️"
CRYPTO_TOAST = "✅ Dirección para el envío"

# ── прочее ──────────────────────────────────────────────────────────────────
UNKNOWN_COMMAND = "❌ Comando desconocido"
GENERIC_ERROR = "❌ Algo salió mal. Inténtalo de nuevo."
OPERATION_CANCELED = "❌ Operación cancelada"
