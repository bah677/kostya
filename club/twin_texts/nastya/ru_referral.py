"""Тексты (RU) для ReferralFeature."""

DEFAULT_INVITE_DISPLAY_NAME = "Участник"

AFFILIATE_BOT_USERNAME_ERROR_HTML = (
    "❌ Не удалось узнать адрес бота для ссылки. "
    "Попробуйте позже или задайте в .env переменную <code>TELEGRAM_BOT_USERNAME</code> "
    "(username без символа @)."
)

AFFILIATE_ERROR_HTML = (
    "❌ Произошла ошибка при формировании реферальной ссылки. Попробуйте позже."
)

REFERRER_NOTIFY_DEFAULT_NAME = "Новый пользователь"

REFERRAL_PAID_BONUS_DONE = "есть оплата подписки; бонус +7 дней начислен"
REFERRAL_PAID_BONUS_PENDING = "есть оплата; бонус скоро будет зафиксирован в системе"
REFERRAL_NOT_PAID = "пока без оплаты подписки"


def affiliate_header_html(*, referral_link_esc: str, referral_link_href: str) -> str:
    return (
        "<b>🤝 Поделись ссылкой с друзьями</b>\n\n"
        f'<a href="{referral_link_href}">{referral_link_esc}</a>\n\n'
    )


def affiliate_stats_block_html(
    *, invites: int, monthly: int, paid: int, bonuses: int
) -> str:
    return (
        "<b>📊 Ваши результаты</b>\n"
        f"• Переходов по вашей ссылке (всего): <b>{invites}</b>\n"
        f"• Новых за последние 30 дней: <b>{monthly}</b>\n"
        f"• Оформили подписку (успешная оплата): <b>{paid}</b>\n"
        f"• Раз начислен бонус +7 дней вам: <b>{bonuses}</b>\n\n"
        "<i>📌 Как это работает:</i>\n"
        "• По вашей ссылке друзья попадут в бота\n"
        "• После первой оплаты подписки вашим рефералом "
        "вы получите +7 дней к лицензии бесплатно!\n\n"
    )


AFFILIATE_RECENT_INVITES_HEADER = "<b>Последние приглашённые</b> (до 5):\n"

AFFILIATE_NO_INVITES_YET = (
    "<i>Пока никто не зашёл по вашей ссылке — отправьте её близким из блока выше.</i>\n\n"
)

AFFILIATE_THANKS_FOOTER = "Спасибо, что делитесь проектом! 🙏"


def invited_line_with_date(*, name_esc: str, date_esc: str, tail_esc: str) -> str:
    return f"• {name_esc} ({date_esc}): {tail_esc}"


def invited_line_no_date(*, name_esc: str, tail_esc: str) -> str:
    return f"• {name_esc}: {tail_esc}"


def referrer_notify_html(*, user_display_esc: str) -> str:
    return (
        "✨ По вашей ссылке в клуб пришёл новый человек.\n\n"
        f"<b>{user_display_esc}</b> теперь с нами.\n"
        "Спасибо, что делитесь этим пространством с теми, кому тоже хочется быть ближе к Богу.\n\n"
        "🎁 Когда ваш друг впервые оплатит подписку, вам добавится <b>+7 дней лицензии в подарок.</b> "
        "Пусть это будет маленькой благодарностью за ваш труд любви.\n\n"
        "<blockquote>Ибо не неправеден Бог, чтобы забыл дело ваше и труд любви, который вы оказали во имя Его…\nЕвреям 6:10</blockquote>"
    )
