"""
Сегменты дожима (фаза 1) и эвристики классификации по переписке в личке.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Сегменты
SEG_REF_COLD = "ref_cold"       # A: ref-ссылка, почти не писал
SEG_ORGANIC_COLD = "organic_cold"  # A: без ref, мало активности
SEG_ENGAGED = "engaged"         # B: живой диалог с агентом
SEG_STUCK_DIALOG = "stuck_dialog"  # B1: тишина после ответа ассистента (120–122)
SEG_CART = "cart"               # C: заказ / смотрел оплату
SEG_REFUSED = "refused"         # D: явный отказ (статус 998)
SEG_SENSITIVE = "sensitive"     # E: горе / насилие — без дожима и без /support

COLD_SEGMENTS = frozenset({SEG_REF_COLD, SEG_ORGANIC_COLD})

SENSITIVE_PATTERNS = re.compile(
    r"|".join(
        [
            r"погиб",
            r"\bумер",
            r"умерл",
            r"смерт",
            r"похорон",
            r"утрат",
            r"не\s+знаю,?\s+как\s+жить",
            r"домашн",
            r"избива",
            r"насил",
            r"суицид",
            r"покончить\s+с\s+собой",
            r"не\s+стало",
        ]
    ),
    re.IGNORECASE,
)

REFUSAL_PATTERNS = re.compile(
    r"(^|\s)(нет|стоп)(\s|$|!)|не\s+интерес|отстан|пока\s+нет",
    re.IGNORECASE,
)

from bot.texts.prompts.followup_segments import SENSITIVE_AGENT_ADDON  # noqa: F401


def text_indicates_sensitive(text: str) -> bool:
    if not text or not text.strip():
        return False
    return bool(SENSITIVE_PATTERNS.search(text))


def texts_indicate_sensitive(texts: List[str]) -> bool:
    return any(text_indicates_sensitive(t) for t in texts if t)


def text_indicates_refusal(text: str) -> bool:
    if not text or not text.strip():
        return False
    return bool(REFUSAL_PATTERNS.search(text.strip()))


def sensitive_context_system_addon(
    user_message: str, history: Optional[List[Dict[str, str]]] = None
) -> bool:
    """Нужно ли агенту отключить продажи и /support в этом ответе."""
    parts = [user_message]
    if history:
        parts.extend(
            m.get("content") or ""
            for m in history
            if m.get("role") == "user"
        )
    return texts_indicate_sensitive(parts)


def start_param_is_ref(start_param: Optional[str]) -> bool:
    return bool(start_param and start_param.startswith("ref_"))


def classify_from_signals(signals: Dict[str, Any]) -> str:
    """Приоритет: отказ → чувствительное → корзина → диалог → ref-холод → органик-холод."""
    if signals.get("refusal"):
        return SEG_REFUSED
    if signals.get("sensitive"):
        return SEG_SENSITIVE
    if signals.get("unpaid_order"):
        return SEG_CART
    if signals.get("meaningful_count", 0) >= 2 and signals.get("assistant_count", 0) >= 1:
        return SEG_ENGAGED
    if signals.get("ref_start") and signals.get("meaningful_count", 0) <= 1:
        return SEG_REF_COLD
    return SEG_ORGANIC_COLD


def pick_topic_snippet(text: str, max_len: int = 80) -> str:
    t = " ".join((text or "").split())
    if not t or t.startswith("/"):
        return ""
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"
