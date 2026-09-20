"""Простая эвристика: похоже ли текст просьбы на прямой денежный перевод."""

from __future__ import annotations

import re

# 16 цифр подряд или с пробелами/дефисами между группами (карта).
_CARD_16_RE = re.compile(
    r"(?<!\d)(?:\d[ \-]*){15}\d(?!\d)",
)

# «PayPal» / «пейпал» рядом с @-адресом (в пределах ~40 символов).
_PAYPAL_WORD_RE = re.compile(r"paypal|пейпал", re.IGNORECASE)
_AT_HANDLE_RE = re.compile(r"@[A-Za-z0-9_]{3,}")

_REQUISITES_RE = re.compile(r"реквизит", re.IGNORECASE)


def looks_like_money_transfer(text: str) -> bool:
    """True, если текст похож на просьбу о переводе денег / реквизитах.

    Консервативно: сомнительные случаи пропускаем (ложный запрет хуже пропуска).
    Короткие числа (телефон и т.п.) сами по себе не триггерят.
    """
    raw = (text or "").strip()
    if not raw:
        return False

    if _REQUISITES_RE.search(raw):
        return True

    if _CARD_16_RE.search(raw):
        return True

    for m in _PAYPAL_WORD_RE.finditer(raw):
        start = max(0, m.start() - 40)
        end = min(len(raw), m.end() + 40)
        window = raw[start:end]
        if _AT_HANDLE_RE.search(window):
            return True

    return False
