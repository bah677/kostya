"""Тексты (RU) для подарка продления участнику клуба."""

from __future__ import annotations

import html
from typing import Any, Dict, Optional

PROMPT_RECIPIENT_HTML = (
    "<b>🎁 Продление подписки в подарок</b>\n\n"
    "Напишите <b>@ник</b> или <b>имя</b> участника клуба, которому хотите подарить продление.\n\n"
    "Получатель должен быть в клубе с <b>активной подпиской</b>."
)
ERR_QUERY_TOO_SHORT = "❌ Введите хотя бы 2 символа — @ник или имя."
ERR_NOT_FOUND = (
    "😔 Не нашли участника с активной подпиской по этому запросу.\n\n"
    "Попробуйте @ник из Telegram или другое написание имени."
)
ERR_SELF = "❌ Нельзя подарить продление самому себе."
PICK_RECIPIENT_HTML = (
    "<b>Нашли несколько участников</b> — выберите, кому подарить:"
)
CONFIRM_RECIPIENT_HTML = "<b>Подарить продление этому участнику?</b>"
CONFIRM_RECIPIENT_ANON_HTML = (
    "<b>🎁 Подарить продление по анонимной просьбе</b>\n\n"
    "Имя автора просьбы не показываем — после оплаты просьба завершится автоматически."
)
TARIFFS_HEADER_HTML = (
    "<b>🎁 Выберите срок продления в подарок</b>\n\n"
    "Получатель: <b>{name}</b>"
)
TARIFFS_HEADER_ANON_HTML = (
    "<b>🎁 Выберите срок продления в подарок</b>\n\n"
    "По анонимной просьбе на доске добрых дел."
)
BTN_CONFIRM = "✅ Да, подарить"
BTN_CANCEL = "❌ Отмена"
BTN_BACK_MENU = "◀️ В меню"
MSG_CANCELLED = "Отменено."

DONOR_SUCCESS_HTML = (
    "✅ <b>Подарок оплачен!</b>\n\n"
    "Вы подарили продление подписки участнику <b>{recipient}</b> "
    "на <b>{duration}</b>.\n\n"
    "Получателю отправлено анонимное уведомление. Спасибо! 🙏"
)

DONOR_SUCCESS_ANON_HTML = (
    "✅ <b>Подарок оплачен!</b>\n\n"
    "Продление на <b>{duration}</b> оформлено по анонимной просьбе.\n\n"
    "Спасибо за доброе дело! 🙏"
)

RECIPIENT_ANONYMOUS_HTML = (
    "🎁 <b>Вам подарили продление подписки в клубе!</b>\n\n"
    "Кто-то из участников клуба оплатил для вас продление на <b>{duration}</b>.\n"
    "📅 <b>Новый срок окончания:</b> {expires}\n\n"
    "Спасибо, что вы с нами! 🙏"
)


def display_name(row: Dict[str, Any]) -> str:
    fn = (row.get("first_name") or "").strip()
    ln = (row.get("last_name") or "").strip()
    full = f"{fn} {ln}".strip()
    un = (row.get("username") or "").strip()
    if full and un:
        return f"{full} (@{un})"
    if full:
        return full
    if un:
        return f"@{un}"
    return f"ID {row.get('user_id')}"


def recipient_button_label(row: Dict[str, Any]) -> str:
    return display_name(row)[:64]


def escape_name(name: str) -> str:
    return html.escape(name or "")
