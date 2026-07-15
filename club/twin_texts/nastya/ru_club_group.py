"""Тексты (RU) для ClubGroupFeature."""

# Колбэк с экрана /subs («В клуб»): одна строка данных — см. фильтр в register_handlers.
SUBS_CLUB_CALLBACK_DATA = "subs_club_access"

CLUB_NOT_CONFIGURED_HTML = (
    "Закрытая группа клуба пока не подключена у бота."
)

CLUB_NO_LICENSE_HTML = (
    "<b>Доступ к закрытому клубу</b> возможен по активной подписке.\n"
    "Оформить: /payment"
)

CLUB_LINK_UNCONFIGURED_HTML = (
    "Ссылка на посты внутри клуба сейчас не настроена у сервера.\n"
    "Напиши в /support — куратор пришлёт, как попасть в чат."
)

CLUB_LINK_ERROR_HTML = (
    "Не удалось получить ссылку. Попробуй позже или /support."
)

BTN_OPEN_CLUB = "📱 Открыть клуб"
BTN_JOIN_CLUB = "🟢 Вступить в клуб"

CLUB_ALREADY_IN_LEAD_HTML = (
    "<b>Ты уже в клубе.</b>\n"
    "Ссылка на клуб"
)

CLUB_INVITE_LEAD_HTML = "<b>Вот твоя персональная ссылка.</b>"

CLUB_ACCESS_FOOTER = "\n👇"

BTN_JOIN_CLOSED_CLUB = "🟢 Вступить в закрытый клуб"


def payment_invite_html(*, inside_block: str, invite_footer: str) -> str:
    return (
        "🎉 <b>Добро пожаловать в закрытый клуб!</b> 🎉\n\n"
        "Ваша подписка активирована! Теперь у вас есть доступ к закрытому "
        "клубу «Настоящая Я».\n\n"
        f"{inside_block}\n\n"
        f"{invite_footer}"
    )


def admin_gift_invite_html(
    *, expires_str: str, inside_block: str, invite_footer: str
) -> str:
    return (
        "🎁 <b>Добро пожаловать в клуб!</b>\n\n"
        f"Мы предоставили вам доступ в закрытый клуб <b>«Настоящая Я»</b> "
        f"в подарок до <b>{expires_str}</b>.\n\n"
        f"{inside_block}\n\n"
        f"{invite_footer}\n\n"
        "Узнать срок доступа: /subs"
    )


def club_inside_block() -> str:
    return (
        "<b>✨ Что вас ждет внутри:</b>\n"
        "• 📡 Прямые эфиры\n"
        "• 📖 Разборыn"
        "• 🎙 Подкасты\n"
        "• 💬 Чат с единомышленниками"
    )


def invite_link_footer(*, ttl_hours: int) -> str:
    return (
        "<b>🔗 Ваша ссылка-приглашение</b>\n"
        f"Она активна {ttl_hours} часов "
        "и может быть использована только один раз.\n\n"
        "Нажмите на кнопку ниже, чтобы вступить в группу 👇"
    )


# --- админские отчёты ночного аудита ---

AUDIT_BATCH_PART_PREFIX = "<i>Часть {part} из {total}</i>\n\n"

AUDIT_REMOVAL_INTRO_TEMPLATE = (
    "⚠️ <b>Закрытый клуб — автоисключение</b>\n"
    "⏰ <code>{ts}</code> · чат <code>{club_group_id}</code>\n"
    "<b>Удалено из группы:</b> <code>{count}</code>\n\n"
    "<i>Ниже — карточка на каждого (оплаты, тариф, активность в группе).</i>"
)

AUDIT_REMOVAL_CARD_FALLBACK_TEMPLATE = "🚪 Исключён (карточка не собралась)\n{bullet}"

AUDIT_CACHE_PRUNE_SUMMARY = (
    "<b>Смысл:</b> человек всё ещё числился в <code>club_group_member_cache</code>, но по "
    "<code>get_chat_member</code> на момент ночного аудита он <b>уже не является участником</b> закрытой группы "
    "(ушёл сам, ограничен без членства и т. п.). Запись в кэше снята для актуальности; "
    "<b>действие ban/unban бота не выполнялось.</b>"
)

AUDIT_CACHE_FIRST_INTRO_TEMPLATE = (
    "ℹ️ <b>Закрытый клуб — синхронизация кэша (без исключения из чата)</b>\n"
    "⏰ <b>Отчёт (UTC):</b> <code>{ts}</code>\n"
    "<b>Чат клуба:</b> <code>{club_group_id}</code>\n"
    "<b>Всего снято с кэша за прогон:</b> <code>{count}</code>\n\n"
    "{summary}\n\n"
    "<b>Список (для информации и сверки):</b>\n"
)

AUDIT_CACHE_CONT_INTRO_TEMPLATE = (
    "<b>Закрытый клуб — продолжение списка (кэш)</b>\n"
    "<i>Тот же отчёт UTC: {ts}</i>\n"
    "<b>Список (продолжение):</b>\n"
)


def audit_bullet_no_user_row(user_id: int) -> str:
    return f"• <code>{user_id}</code> (нет строки в <code>users</code>)"


def audit_bullet_user(
    *, user_id: int, username: str, name_part: str
) -> str:
    return f"• <code>{user_id}</code>{username}{name_part}"


def telegram_cache_prune_note_left() -> str:
    return "в Telegram больше не в группе (left)"


def telegram_cache_prune_note_kicked() -> str:
    return "удалён из чата по данным Telegram (kicked)"


def telegram_cache_prune_note_restricted() -> str:
    return "ограничен без членства в чате (restricted)"


def telegram_cache_prune_note_status(tail: str) -> str:
    return f"текущий статус Telegram: {tail}"
