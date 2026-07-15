"""Пользовательские тексты (RU) для `SupportFeature`."""

from __future__ import annotations

import html

TOPIC_FEEDBACK = "Обратная связь"
TOPIC_SUPPORT = "Обращение в поддержку"

MSG_EMPTY = "❌ Сообщение не может быть пустым. Напишите ваш вопрос или отзыв:"
MSG_CREATE_FAILED = "❌ Не удалось создать обращение. Попробуйте позже."
MSG_ERROR_GENERIC = "❌ Произошла ошибка. Попробуйте позже."
MSG_FEEDBACK_THANKS = "Спасибо, что помогаете нам становиться лучше! 🙏"

NO_USERNAME = "нет username"

SUPPORT_START_HTML = (
    "<b>📞 Служба поддержки</b>\n\n"
    "<b>💬 Опишите вашу проблему подробно:</b>\n"
    "• Что произошло?\n"
    "• Какие действия привели к проблеме?\n"
    "• Какой результат ожидали?\n\n"
    "Чем подробнее опишете - тем быстрее поможем! 🛠️"
)

FEEDBACK_START_HTML = (
    "<b>💬 Обратная связь</b>\n\n"
    "Мы всегда рады услышать ваше мнение! Ваши отзывы помогают нам становиться лучше.\n\n"
    "<b>📝 Напишите, что вы думаете о клубе:</b>\n"
    "• Что вам нравится?\n"
    "• Что можно улучшить?\n"
    "• Есть ли пожелания?\n\n"
    "Любые идеи и предложения — всё важно! 🙏"
)

ADMIN_TITLE_FEEDBACK = "💬 <b>НОВАЯ ОБРАТНАЯ СВЯЗЬ</b>"
ADMIN_TITLE_SUPPORT = "🆕 <b>НОВЫЙ ТИКЕТ ПОДДЕРЖКИ</b>"

MEDIA_TITLE_FEEDBACK = "💬 <b>Вложение (обратная связь)</b>"
MEDIA_TITLE_SUPPORT = "📎 <b>Вложение к тикету</b>"


def ticket_created_html(*, ticket_number: str, created_time: str, content: str) -> str:
    snippet = html.escape(content[:200]) + ("..." if len(content) > 200 else "")
    return (
        f"✅ <b>Обращение создано!</b>\n\n"
        f"🎫 <b>Номер тикета:</b> <code>{html.escape(ticket_number)}</code>\n"
        f"📊 <b>Статус:</b> 🔴 Открыт\n"
        f"⏰ <b>Создан:</b> {created_time}\n\n"
        f"<b>💬 Ваше сообщение:</b>\n{snippet}\n\n"
        f"Мы ответим в течение <b>24 часов</b>."
    )


def admin_ticket_notification_html(
    *,
    title: str,
    ticket_number: str,
    user_name: str,
    username_str: str,
    user_id: int,
    esc_msg: str,
    created_time: str,
) -> str:
    return (
        f"{title}\n\n"
        f"🎫 <b>Номер:</b> <code>{html.escape(ticket_number)}</code>\n"
        f"👤 <b>Пользователь:</b> {html.escape(user_name)} ({html.escape(username_str)})\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
        f"⏰ <b>Создан:</b> {created_time}\n\n"
        f"💬 <b>Сообщение:</b>\n{esc_msg}\n\n"
    )


def support_ticket_reply_html(*, ticket_number: str, admin_response: str) -> str:
    """Ответ пользователю по тикету (SupportFeature и admin_console)."""
    esc = html.escape(admin_response or "")
    return (
        f"📬 <b>Ответ от службы поддержки</b>\n\n"
        f"По вашему обращению <b>#{html.escape(str(ticket_number))}</b> получен ответ:\n\n"
        f"<i>{esc}</i>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Если у вас остались вопросы, создайте новое обращение через команду /support"
    )


def ticket_media_caption_html(
    *,
    title: str,
    ticket_number: str,
    user_full_name: str,
    username_str: str,
    user_id: int,
) -> str:
    return (
        f"{title}\n\n"
        f"🎫 <b>Номер:</b> <code>{html.escape(ticket_number)}</code>\n"
        f"👤 <b>Пользователь:</b> {html.escape(user_full_name)} ({html.escape(username_str)})\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>"
    )
