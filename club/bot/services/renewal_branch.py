"""Ветки писем о продлении (ПРО-1…3): origin + жизнь в группе."""

from __future__ import annotations

from typing import Any, Dict, Optional

ORIGIN_PAYMENT = "payment"
ORIGIN_GIFT = "gift"
ORIGIN_TRIAL = "trial"

BRANCH_LIVING = "living"  # 10+
BRANCH_PEEKING = "peeking"  # 1–9
BRANCH_SILENT = "silent"  # 0

_ORIGIN_LABELS = {
    ORIGIN_GIFT: "подарок",
    ORIGIN_TRIAL: "пробная неделя",
    ORIGIN_PAYMENT: "полная оплата",
}

_BRANCH_LABELS = {
    BRANCH_SILENT: "молчит",
    BRANCH_PEEKING: "заглядывает (1–9)",
    BRANCH_LIVING: "живёт (10+)",
}


def normalize_license_origin(raw: Optional[str], *, license_type: Optional[str] = None) -> str:
    o = (raw or "").strip().lower()
    if o in (ORIGIN_GIFT, ORIGIN_TRIAL, ORIGIN_PAYMENT):
        return o
    if o == "bonus":
        return ORIGIN_PAYMENT
    if (license_type or "") == "admin_grant":
        return ORIGIN_GIFT
    if o.startswith("promo") or "trial" in o or "test1week" in o or "test2week" in o:
        return ORIGIN_TRIAL
    if not o:
        return ORIGIN_PAYMENT
    return ORIGIN_PAYMENT


def group_life_branch(msgs_30d: int) -> str:
    n = max(0, int(msgs_30d or 0))
    if n <= 0:
        return BRANCH_SILENT
    if n <= 9:
        return BRANCH_PEEKING
    return BRANCH_LIVING


def origin_label(origin: str) -> str:
    return _ORIGIN_LABELS.get(origin, origin)


def branch_label(branch: str) -> str:
    return _BRANCH_LABELS.get(branch, branch)


def reminder_keyboard_for_branch(branch: str) -> Optional[str]:
    """Клавиатура письма о продлении: кнопка оплаты всегда (решение продукта)."""
    return "payment_extend"


def enrich_profile_for_renewal(
    profile: Optional[Dict[str, Any]],
    *,
    origin: str,
    msgs_30d: Optional[int] = None,
) -> Dict[str, Any]:
    """Копия профиля с полями для промпта."""
    out = dict(profile or {})
    msgs = msgs_30d if msgs_30d is not None else int(out.get("group_msgs_30d") or 0)
    out["group_msgs_30d"] = msgs
    out["_renewal_origin"] = origin
    out["_renewal_branch"] = group_life_branch(msgs)
    return out
