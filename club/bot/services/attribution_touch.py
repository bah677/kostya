"""Парсинг маркетинговых касаний: /start payload и payment-колбэки."""

from __future__ import annotations

import html as html_mod
import re
from dataclasses import dataclass
from typing import Optional

_START_CMD_RE = re.compile(
    r"^/start(?:@[A-Za-z0-9_]+)?(?:\s+(.*))?$",
    re.IGNORECASE | re.DOTALL,
)
_REF_PAYLOAD_RE = re.compile(r"^ref_([a-zA-Z0-9_]+)")

_MARKETING_CALLBACK_PREFIXES = (
    "payment_start",
    "payment_select_promo_",
    "payment_select_",
    "payment_currency_rub_",
    "payment_currency_usd_",
)

_MEANINGFUL_SKIP = frozenset(
    {
        "/start",
        "/help",
        "/support",
        "/subscription",
        "/cancel",
    }
)


@dataclass(frozen=True)
class ParsedTouch:
    touch_key: str
    touch_kind: str
    ref_key: Optional[str] = None
    raw_payload: Optional[str] = None


def parse_start_text(text: str) -> Optional[ParsedTouch]:
    raw = (text or "").strip()
    if not raw:
        return None
    mo = _START_CMD_RE.match(raw)
    if not mo:
        return None
    rest = (mo.group(1) or "").strip()
    if not rest:
        return None
    first_token = rest.split()[0]
    if first_token.startswith("ref_"):
        ref = first_token[4:]
        if ref:
            return ParsedTouch(
                touch_key=f"ref_{ref}",
                touch_kind="ref",
                ref_key=ref,
                raw_payload=rest[:500],
            )
    if _is_marketing_start_payload(first_token):
        return ParsedTouch(
            touch_key=first_token[:200],
            touch_kind="start_payload",
            raw_payload=rest[:500],
        )
    return None


def parse_start_payload(payload: str) -> Optional[ParsedTouch]:
    p = (payload or "").strip()
    if not p:
        return None
    first = p.split()[0]
    if first.startswith("ref_"):
        ref = first[4:]
        if ref:
            return ParsedTouch(
                touch_key=f"ref_{ref}",
                touch_kind="ref",
                ref_key=ref,
                raw_payload=p[:500],
            )
    if _is_marketing_start_payload(first):
        return ParsedTouch(
            touch_key=first[:200],
            touch_kind="start_payload",
            raw_payload=p[:500],
        )
    return None


def _is_marketing_start_payload(token: str) -> bool:
    if token.startswith("ref_"):
        return True
    if token.startswith("payment_start"):
        return True
    if token.startswith("promo_"):
        return True
    if token == "benefit3":
        return True
    return False


def parse_callback_data(data: str) -> Optional[ParsedTouch]:
    cb = (data or "").strip()
    if not cb:
        return None
    if not any(cb.startswith(p) for p in _MARKETING_CALLBACK_PREFIXES):
        return None
    if cb == "payment_start":
        return ParsedTouch(touch_key="payment_start", touch_kind="payment_callback", raw_payload=cb)
    if cb.startswith("payment_start_promo_"):
        return ParsedTouch(
            touch_key=cb[:200],
            touch_kind="payment_callback",
            raw_payload=cb,
        )
    if cb.startswith("payment_start_") and cb != "payment_start":
        suffix = cb[len("payment_start_") :]
        if suffix and not suffix.startswith("promo_"):
            return ParsedTouch(
                touch_key=cb[:200],
                touch_kind="payment_callback",
                raw_payload=cb,
            )
    if cb.startswith("payment_select_promo_"):
        return ParsedTouch(
            touch_key=cb[:200],
            touch_kind="payment_callback",
            raw_payload=cb,
        )
    return ParsedTouch(
        touch_key=cb[:200],
        touch_kind="payment_callback",
        raw_payload=cb,
    )


_CHECKOUT_TOUCH_PREFIXES = (
    "payment_select_",
    "payment_currency_rub_",
    "payment_currency_usd_",
)


def is_checkout_step_touch_key(touch_key: Optional[str]) -> bool:
    """Шаги выбора тарифа/валюты — не источник оплаты для аналитики."""
    k = (touch_key or "").strip()
    if not k:
        return False
    return any(k.startswith(p) for p in _CHECKOUT_TOUCH_PREFIXES)


def ref_key_for_lookup(touch_key: Optional[str]) -> Optional[str]:
    """Ключ в таблице ref_keys (без префикса ref_)."""
    k = (touch_key or "").strip()
    if not k.startswith("ref_"):
        return None
    return (k[4:] or "").strip() or None


def format_touch_key_plain(
    touch_key: Optional[str], ref_name: Optional[str] = None
) -> str:
    k = (touch_key or "").strip()
    if not k:
        return "—"
    if ref_name:
        return f"{ref_name} ({k})"
    if k.startswith("payment_start_promo_"):
        suffix = k.replace("payment_start_promo_", "", 1)
        if suffix.startswith("promo_test1week") or suffix.startswith("test1week"):
            return f"promo_week ({suffix})"
    if k in ("promo_week",) or k.startswith("promo_week_"):
        return k
    if k == "payment_start":
        return "меню оплаты (payment_start)"
    return k


def format_touch_key_html(
    touch_key: Optional[str], ref_name: Optional[str] = None
) -> str:
    plain = format_touch_key_plain(touch_key, ref_name)
    if plain == "—":
        return plain
    k = (touch_key or "").strip()
    if ref_name and k:
        return f"{html_mod.escape(ref_name)} (<code>{html_mod.escape(k)}</code>)"
    if k.startswith("payment_start_promo_"):
        suffix = k.replace("payment_start_promo_", "", 1)
        if suffix.startswith("promo_test1week") or suffix.startswith("test1week"):
            return f"promo_week (<code>{html_mod.escape(suffix)}</code>)"
    if k in ("promo_week",) or k.startswith("promo_week_"):
        return f"<code>{html_mod.escape(k)}</code>"
    if k == "payment_start":
        return "меню оплаты (<code>payment_start</code>)"
    return f"<code>{html_mod.escape(k)}</code>"


def is_meaningful_user_message(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    low = t.lower().split()[0] if t.split() else ""
    if low in _MEANINGFUL_SKIP:
        return False
    if low.startswith("/"):
        return False
    return True
