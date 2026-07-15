"""Тексты (RU) для PaymentFeature."""

TARIFFS_UNAVAILABLE = "❌ Тарифы временно недоступны. Попробуйте позже."

TARIFFS_HEADER_GIFT = "🎁 <b>Выберите тариф для подарка</b>\n"
TARIFFS_HEADER_SUBSCRIPTION = "💰 <b>Выберите подписку</b>\n\n🎁 <b>Специальные цены:</b>\n"
TARIFFS_FOOTER = "\n👇 Нажмите на кнопку, чтобы выбрать тариф"

BTN_GIFT_SUBSCRIPTION = "🎁 Подарить подписку"
BTN_GIFT_CONTINUE = "🎁 Продолжить"
BTN_BACK = "◀️ Назад"
BTN_GO_TO_PAYMENT = "💳 Перейти к оплате"
BTN_DOWNLOAD_OFFER_PDF = "📄 Скачать публичную оферту (PDF)"
BTN_SELECT_PROMO_TARIFF = "🎁 Выбрать промо-тариф"

PROMO_UNAVAILABLE = "❌ Промо-предложение недоступно. Попробуйте позже."
PROMO_UNAVAILABLE_ALERT = "❌ Промо-предложение недоступно"
UNKNOWN_COMMAND_ALERT = "❌ Неизвестная команда"
DATA_ERROR_ALERT = "❌ Ошибка при обработке данных"
GENERIC_ERROR_ALERT = "❌ Произошла ошибка"

TARIFF_NOT_FOUND = "❌ Тариф не найден"
PRICE_NOT_FOUND = "❌ Цена для выбранной валюты не найдена"
ORDER_CREATE_FAILED = "❌ Не удалось создать заказ. Попробуйте позже."
PAYMENT_CREATE_FAILED = "❌ Не удалось создать платеж. Попробуйте позже."

BOT_USERNAME_ERROR_HTML = (
    "❌ Не удалось создать ссылку на оплату: не удалось узнать username бота. "
    "Попробуйте позже или укажите в .env <code>TELEGRAM_BOT_USERNAME</code> "
    "(без символа @)."
)

OFFER_INTRO_HTML = (
    "⚖️ <b>Важно:</b> оплачивая подписку, вы подтверждаете, что ознакомились "
    "и соглашаетесь с условиями публичной оферты.\n\n"
    "🔗 Перейдите к оплате кнопкой ниже."
)

OFFER_NOT_CONNECTED_ALERT = "📄 Оферта не подключена. Напишите в поддержку."
OFFER_PDF_CAPTION = "<b>Публичная оферта</b>"
OFFER_SEND_FAILED_ALERT = (
    "❌ Не удалось отправить файл. Обновите оферту в настройках бота или попробуйте позже."
)
OFFER_RATE_LIMIT_ALERT = "⏳ Подождите немного перед повторной отправкой оферты."

PAYMENT_NOT_FOUND = "❌ Платеж не найден"
FULFILLMENT_NOT_CONFIGURED_ALERT = "❌ Контур оплаты не сконфигурирован"
INTERNATIONAL_PAYMENT_UNAVAILABLE_ALERT = "❌ Международная оплата недоступна"
PAYMENT_PENDING_ALERT = "⏳ Платеж еще обрабатывается. Попробуйте через несколько минут."
PAYMENT_NOT_FOUND_OR_CANCELLED_ALERT = (
    "❌ Платеж не найден или отменен. Попробуйте снова."
)
ORDER_NOT_FOUND_ALERT = "❌ Заказ не найден"
AMOUNT_RECALC_ERROR_ALERT = "❌ Ошибка пересчёта суммы"
PAYMENT_RECORD_FAILED_ALERT = "❌ Не удалось зафиксировать оплату"

PAYMENT_CANCELLED = "❌ Оплата отменена"

PAYMENT_SUCCESS_GIFT_HTML = (
    "✅ <b>Оплата подтверждена!</b>\n\n"
    "Подарочная ссылка отправлена вам в сообщении ниже."
)
PAYMENT_SUCCESS_GENERIC_HTML = "✅ <b>Оплата подтверждена!</b>"

PROMO_TARIFFS_UNAVAILABLE = "❌ Промо-тарифы временно недоступны. Попробуйте позже."
PROMO_TARIFFS_HEADER = "🎁 <b>Промо-тарифы</b>\n\n"

PROMO_WELCOME_TEST1WEEK_HTML = (
    "<b>🎁 Пробный доступ на 1 неделю!</b>\n\n"
    "Вам открыт специальный тариф — всего 299₽ за 7 дней.\n\n"
    "<b>✅ Что входит:</b>\n"
    "• Полный доступ в закрытый клуб\n"
    "• Участие во всех эфирах\n"
    "• Молитвы и разборы\n"
    "• Общение с единомышленниками\n\n"
    "<b>👇 Нажмите кнопку ниже, чтобы выбрать тариф</b>"
)

PROMO_WELCOME_GENERIC_HTML = (
    "<b>🎁 Специальное предложение!</b>\n\n"
    "Для вас доступен промо-тариф.\n\n"
    "<b>👇 Нажмите кнопку ниже, чтобы выбрать тариф</b>"
)

CURRENCY_CHOICE_FOOTER = "\n👇 Выберите способ оплаты:"

DEFAULT_TARIFF_NAME = "подписка"


def tariffs_header_with_promo(*, name: str, percent: int) -> str:
    return (
        f"🎯 <b>Персональная акция: {name}</b>\n"
        f"Скидка <b>{percent}%</b> на все тарифы ниже (до первой оплаты):\n\n"
        f"💰 <b>Выберите подписку</b>\n"
    )


def tariff_line_rub(*, name: str, current: int, old: int | None = None) -> str:
    if old:
        return f"• {name} – {current}₽ (вместо {old}₽)"
    return f"• {name} – {current}₽"


def tariff_line_usd(*, name: str, current: int, old: int | None = None) -> str:
    if old:
        return f"   {name} – ${current} (вместо ${old})\n"
    return f"   {name} – ${current}\n"


def tariff_select_button(*, name: str) -> str:
    return f"✅ {name}"


def gift_info_html(*, gift_days_ru: str) -> str:
    return (
        "<b>🎁 Как работает подарок?</b>\n\n"
        "1️⃣ Вы выбираете тариф и оплачиваете подарок\n"
        "2️⃣ Вы получаете специальную ссылку для получателя\n"
        "3️⃣ Вы отправляете ссылку тому, кому хотите подарить подписку\n"
        "4️⃣ Получатель переходит по ссылке и активирует подарок\n"
        "5️⃣ После активации у него открывается доступ в закрытый клуб\n\n"
        "<b>🔐 Важно:</b>\n"
        "• Ссылка <b>одноразовая</b> — после активации станет недействительной\n"
        f"• Срок активации — <b>{gift_days_ru}</b>. Если получатель не активирует подарок, ссылка сгорит\n"
        "• Сама подписка начнет действовать <b>с момента активации</b> получателем\n\n"
        "Нажмите «Продолжить», чтобы выбрать тариф для подарка 👇"
    )


def currency_choice_header(*, tariff_name: str) -> str:
    return f"💳 <b>Оплата подписки {tariff_name}</b>\n\n💰 <b>Цены:</b>\n"


def currency_rub_text_and_button(
    *, current: int, old: int | None = None
) -> tuple[str, str]:
    if old:
        text_line = f"🇷🇺 Картой РФ: {current}₽ (вместо {old}₽)"
        button_text = f"🇷🇺 Картой РФ - {current}₽ (вместо {old}₽)"
    else:
        text_line = f"🇷🇺 Картой РФ: {current}₽"
        button_text = f"🇷🇺 Картой РФ - {current}₽"
    return text_line, button_text


def currency_usd_text_and_button(
    *, current: int, old: int | None = None
) -> tuple[str, str]:
    if old:
        text_line = f"🌍 Картой не РФ: ${current} (вместо ${old})"
        button_text = f"🌍 Картой не РФ - ${current} (вместо ${old})"
    else:
        text_line = f"🌍 Картой не РФ: ${current}"
        button_text = f"🌍 Картой не РФ - ${current}"
    return text_line, button_text


def subscription_payment_description(*, tariff_name: str, currency: str) -> str:
    suffix = "картой РФ" if currency == "rub" else "international card"
    return f"Подписка {tariff_name} ({suffix})"


def checkout_message_html(
    *,
    tariff_name: str,
    amount,
    currency_code: str,
    duration_days: int,
) -> str:
    return (
        f"💳 <b>Оплата подписки {tariff_name}</b>\n\n"
        f"💰 <b>Сумма:</b> {amount} {currency_code}\n"
        f"📅 <b>Срок:</b> {duration_days} дней\n\n"
        f"{OFFER_INTRO_HTML}\n"
    )


def payment_success_subscription_html(*, tariff_name: str, exp: str) -> str:
    return (
        f"✅ <b>Оплата прошла успешно!</b>\n\n"
        f"🎉 Подписка <b>{tariff_name}</b> активирована.\n"
        f"📅 <b>Дата окончания:</b> {exp}\n\n"
        "Спасибо за вашу поддержку! ❤️"
    )
