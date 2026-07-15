"""Тексты (RU) для GiftActivationFeature."""

GIFT_DONOR_DEFAULT_NAME = "Пользователь"

gift_activation_in_progress = "⏳ Подождите, активация уже выполняется..."

GIFT_NOT_FOUND_HTML = (
    "❌ <b>Подарок не найден</b>\n\n"
    "Проверьте правильность ссылки или обратитесь к тому, кто вам её отправил."
)


def gift_already_used_html(*, activated_at_str: str) -> str:
    return (
        "<b>❌ Этот подарок уже использован</b>\n\n"
        f"Подарок был активирован {activated_at_str}."
    )


def gift_expired_html(*, expires_at_str: str) -> str:
    return (
        f"<b>⏰ Срок действия подарка истек</b>\n\n"
        f"Подарок был активен до {expires_at_str}."
    )


GIFT_SELF_ACTIVATE_HTML = (
    "<b>❌ Нельзя активировать свой подарок</b>\n\n"
    "Вы не можете активировать подарок, который сами оплатили."
)

GIFT_TARIFF_MISSING_HTML = (
    "❌ <b>Ошибка</b>\n\n"
    "Тариф для этого подарка не найден. Обратитесь в поддержку."
)


def gift_activated_html(*, tariff_name: str, expiry_str: str) -> str:
    return (
        "<b>🎉 Подарок активирован!</b>\n\n"
        "Добро пожаловать в клуб «Разговоры с Богом»!\n\n"
        f"📋 <b>Тариф:</b> {tariff_name}\n"
        f"📅 <b>Доступ до:</b> {expiry_str}\n\n"
    )


def gift_donor_notify_html(*, donor_name: str) -> str:
    return (
        f"🎉 <b>Ваш подарок активирован!</b>\n\n"
        f"<b>{donor_name}</b> активировал подарок и теперь в клубе.\n\n"
        f"Спасибо, что делитесь добром! 🙏"
    )


GIFT_ERROR_HTML = (
    "❌ <b>Произошла ошибка при активации подарка</b>\n\n"
    "Пожалуйста, попробуйте позже или обратитесь в поддержку."
)
