"""Пользовательские тексты (RU) для FollowupFeature."""

STUCK_CTA_TEXT = (
    "Если хотите глубже разобрать вашу ситуацию — клуб открыт.\n"
    "Попробуйте месяц за 1200₽."
)

DEFAULT_FIRST_NAME = "друг"
DEFAULT_TOPIC_SNIPPET = "ваш вопрос"

BTN_STUCK_GET_ANSWER = "Получить ответ"
BTN_JOIN_CLUB = "Вступить в клуб"
BTN_PAYMENT_STANDARD = "💰 Стандартные тарифы"
BTN_PAYMENT_PROMO_WEEK = "🎁 Пробная неделя — 299₽"

stuck_callback_stale_alert = "Сообщение устарело"
stuck_callback_already_sent_alert = "Ответ уже отправлялся"
stuck_building_status = "Секунду, собираю выжимку по вашей теме…"
stuck_build_failed = "Не удалось собрать ответ. Напишите в чат — продолжим диалог."

admin_mirror_no_username = "нет username"
admin_mirror_status_waiting_order = "ожидание заказа"
admin_mirror_status_waiting_payment = "ожидание оплаты"
admin_mirror_default_user_name = "Пользователь"


def admin_ai_followup_mirror_html(
    *,
    user_name: str,
    username_str: str,
    user_id: int,
    status_text: str,
    escaped_message: str,
) -> str:
    return (
        f"🤖 <b>AI Followup сообщение</b>\n\n"
        f"👤 <b>Пользователь:</b> {user_name} ({username_str})\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
        f"📋 <b>Статус:</b> {status_text}\n\n"
        f"💬 <b>Сообщение:</b>\n{escaped_message}"
    )
