"""Тексты (RU) для MessagingFeature."""

DEFAULT_USER_DISPLAY_NAME = "Пользователь"

AGENT_NO_REPLY_HTML = (
    "🙏 Спасибо за ваш вопрос! Я передал его нашим кураторам. "
    "Они ответят вам в ближайшее время."
)
AGENT_TIMEOUT_RETRY_HTML = (
    "🙏 Для ответа мне нужно чуть больше времени, чем обычно. "
    "Я уже пробую снова и пришлю ответ автоматически."
)
MEDIA_EMPTY_HTML = (
    "🎧 Не удалось разобрать вложение. "
    "Попробуйте голосовое с вопросом, фото с подписью или текст."
)
MEDIA_HANDLER_ERROR_HTML = (
    "⚠️ Не получилось обработать вложение. Попробуйте ещё раз или напишите текстом."
)

QUICK_REPLY_INVALID_DATA_ALERT = "Некорректные данные"
QUICK_REPLY_ERROR_ALERT = "Ошибка"
QUICK_REPLY_STALE_ALERT = "Кнопки устарели — ответьте текстом в чат"

BTN_JOIN_CLUB = "🚪 Вступить в клуб"
BTN_PROMO_WEEK = "✨ 1 неделя за 299 ₽"

# --- админская пересылка диалогов ---

ADMIN_SOURCE_INLINE_NOTE = (
    "🔘 <b>Источник реплики:</b> нажата inline-кнопка под ответом агента "
    "(ниже — подпись кнопки; пользователь <b>не вводил</b> этот текст вручную).\n\n"
)

ADMIN_SOURCE_MEMBER_AGENT_NOTE = (
    "🏠 <b>Агент:</b> клубный (участник с активной подпиской).\n\n"
)

ADMIN_NO_USERNAME = "(без username)"

def admin_identity_header(
    *,
    ts: str,
    user_disp: str,
    un_part: str,
    user_id: int,
    start_src_esc: str,
) -> str:
    return (
        f"⏰ {ts}\n"
        f"👤 {user_disp} {un_part}\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"Источник: {start_src_esc}\n\n"
    )


def admin_onboarding_button_question(label: str) -> str:
    return f"кнопка [{label}]"


def admin_legacy_dialog_with_answer(
    *,
    source_note: str,
    identity_header: str,
    escaped_question: str,
    escaped_answer: str,
    member_agent: bool = False,
) -> str:
    answer_label = (
        "🏠 <b>Ответ клубного агента:</b>"
        if member_agent
        else "🤖 <b>Ответ агента:</b>"
    )
    return (
        f"💬 <b>Диалог с агентом</b>\n\n"
        f"{source_note}"
        f"{identity_header}"
        f"❓ <b>Вопрос:</b>\n{escaped_question}\n\n"
        f"{answer_label}\n{escaped_answer}"
    )


def admin_legacy_dialog_no_answer(
    *,
    source_note: str,
    identity_header: str,
    escaped_question: str,
) -> str:
    return (
        f"❓ <b>Вопрос пользователя (без ответа агента)</b>\n\n"
        f"{source_note}"
        f"{identity_header}"
        f"💬 <b>Сообщение:</b>\n{escaped_question}"
    )


def admin_forum_dialog_with_answer(
    *,
    preamble: str,
    escaped_question: str,
    escaped_answer: str,
    member_agent: bool = False,
) -> str:
    answer_label = (
        "🏠 <b>Ответ клубного агента:</b>"
        if member_agent
        else "🤖 <b>Ответ агента:</b>"
    )
    return (
        f"{preamble}"
        f"❓ <b>Вопрос:</b>\n{escaped_question}\n\n"
        f"{answer_label}\n{escaped_answer}"
    )


def admin_forum_dialog_no_answer(
    *,
    preamble: str,
    escaped_question: str,
) -> str:
    return (
        f"{preamble}"
        f"💬 <b>Сообщение:</b>\n{escaped_question}"
    )


def admin_timeout_retry_exhausted_alert(
    *,
    user_id: int,
    user_display: str,
    username_part: str,
    escaped_question: str,
) -> str:
    return (
        "🚨 <b>DeepSeek не ответил после ретраев</b>\n\n"
        f"👤 {user_display} {username_part}\n"
        f"🆔 ID: <code>{user_id}</code>\n\n"
        "❓ <b>Последний вопрос:</b>\n"
        f"{escaped_question}\n\n"
        "Нужен ручной разбор/ответ от поддержки."
    )
