"""Тексты (RU) для админ-промо (`/new_promo`) — бот Насти."""

from __future__ import annotations

import html
from typing import Any

BTN_CONFIRM_CREATE = "✅ Создать"
BTN_CANCEL = "❌ Отмена"

MSG_CANCELLED = "❌ Создание промо-кампании отменено."
ERR_NO_ACCESS = (
    "⛔ Нет доступа. Telegram ID должен быть в таблице <code>admins</code>."
)
NEW_PROMO_PROMPT_HTML = (
    "🎯 <b>Новая промо-кампания</b>\n\n"
    "Введите <b>название</b> (для админки и агента):"
)
PROMPT_DESCRIPTION_HTML = "📝 Введите <b>описание</b> акции (видит агент и можно цитировать юзеру):"
PROMPT_DISCOUNT_HTML = (
    "💸 Введите <b>скидку в процентах</b> от базовых тарифов:\n"
    "Пример: <code>15</code>"
)
ERR_NAME_EMPTY = "❌ Название пустое или длиннее 255 символов."
ERR_DESCRIPTION_EMPTY = "❌ Описание не может быть пустым."
ERR_DISCOUNT_FORMAT = "❌ Нужно число от 1 до 99, например <code>15</code>"
MSG_CREATING = "⏳ Создаём кампанию…"
MSG_NOT_SAVED = "❌ Не сохранено."
MSG_CALLBACK_CANCELLED = "❌ Отменено."


def confirm_promo_html(
    *,
    name: str,
    description: str,
    discount_percent: float,
) -> str:
    return (
        "📋 <b>Проверка промо</b>\n"
        f"• Название: <code>{html.escape(name)}</code>\n"
        f"• Описание:\n<pre>{html.escape(description)}</pre>\n"
        f"• Скидка: <b>{discount_percent:g}%</b> от базовых тарифов\n"
        f"• Ссылка будет сгенерирована после создания"
    )


def promo_created_html(*, guid: str, deeplink: str, discount_percent: Any) -> str:
    return (
        "✅ <b>Промо-кампания создана</b>\n"
        f"• GUID: <code>{html.escape(guid)}</code>\n"
        f"• Скидка: <b>{discount_percent:g}%</b>\n"
        f"• Ссылка:\n<code>{html.escape(deeplink)}</code>\n\n"
        "Пользователь, перешедший по ссылке, получит скидку на базовые тарифы "
        "до первой оплаты (включая продление)."
    )
