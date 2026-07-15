"""Правила CTA продающего агента: первый ответ, возражение «дорого»."""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from bot.utils.telegram_html import html_to_plain

# Явный интерес к оплате/цене в первом сообщении пользователя.
_EXPLICIT_PAYMENT_SUBSTRINGS = (
    "оплат",
    "сколько стоит",
    "сколько будет",
    "какая цена",
    "какая стоимость",
    "стоимость",
    "цена",
    "тариф",
    "прайс",
    "купить",
    "оформить подписк",
    "оформить доступ",
    "вступить в клуб",
    "вступить в сообщество",
    "как вступ",
    "как оплат",
    "как заплат",
    "сколько ₽",
    "сколько руб",
)

# Явное возражение по цене (только тогда — пробная неделя).
_PRICE_OBJECTION_SUBSTRINGS = (
    "дорого",
    "дороговато",
    "слишком дорого",
    "не потяну",
    "не тяну",
    "не по карману",
    "не могу позволить",
    "нет денег",
    "не хватит денег",
    "не хватит средств",
    "много денег",
    "слишком много",
    "не подъем",
    "не подъём",
    "не по бюджет",
    "не в бюджет",
)


def _norm(text: str) -> str:
    t = html_to_plain(text or "").lower()
    return t.replace("«", "").replace("»", "").replace('"', "")


def text_has_explicit_payment_intent(text: str) -> bool:
    t = _norm(text)
    if not t or t in ("/start", "start"):
        return False
    return any(s in t for s in _EXPLICIT_PAYMENT_SUBSTRINGS)


def text_has_price_objection(text: str) -> bool:
    t = _norm(text)
    if not t:
        return False
    return any(s in t for s in _PRICE_OBJECTION_SUBSTRINGS)


def analyze_private_history_for_sales_cta(
    history: Sequence[Dict[str, str]],
) -> Tuple[int, str]:
    """Число прошлых ответов ассистента и первое содержательное сообщение user."""
    assistant_count = 0
    first_user = ""
    for msg in history:
        role = (msg.get("role") or "").strip().lower()
        content = (msg.get("content") or "").strip()
        if role == "assistant":
            assistant_count += 1
        elif role == "user" and not first_user:
            if content and content.lower() not in ("/start", "start"):
                first_user = content
    return assistant_count, first_user


def apply_sales_cta_policy(
    *,
    wants_subscribe: bool,
    wants_promo_week: bool,
    assistant_replies_in_history: int,
    first_user_message: str,
    current_user_message: str,
) -> Tuple[bool, bool]:
    """
    Программные ограничения поверх маркеров LLM.
    Первый ответ агента — без оплаты, если в первом сообщении user не про оплату.
    Пробная неделя — только при явном «дорого» в текущем сообщении user.
    """
    if assistant_replies_in_history == 0:
        if not text_has_explicit_payment_intent(first_user_message):
            wants_subscribe = False
            wants_promo_week = False

    if wants_promo_week and not text_has_price_objection(current_user_message):
        wants_promo_week = False

    if wants_promo_week:
        wants_subscribe = False

    return wants_subscribe, wants_promo_week
