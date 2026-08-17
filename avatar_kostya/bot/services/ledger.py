"""Расчёт сумм журнала: брутто / комиссия / нетто и влияние на депозит."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional, Tuple

MONEY_Q = Decimal("0.00000001")
DEFAULT_CURRENCY = "USDT"


def parse_amount(text: str) -> Optional[Decimal]:
    raw = (text or "").strip().replace(" ", "").replace(",", ".")
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite():
        return None
    return _q(value)


def _q(value: Decimal) -> Decimal:
    return value.quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def format_money(amount, currency: str = DEFAULT_CURRENCY) -> str:
    d = _q(Decimal(str(amount or 0)))
    s = format(d, "f").rstrip("0").rstrip(".")
    if s in ("", "-"):
        s = "0"
    return f"{s} {currency}"


def deposit_amounts(gross: Decimal, fee: Decimal) -> Tuple[Decimal, Decimal]:
    """Возвращает (нетто, delta остатка). Нетто = брутто − комиссия."""
    net = _q(gross - fee)
    return net, net


def expense_amounts(gross: Decimal, fee: Decimal) -> Tuple[Decimal, Decimal]:
    """Нетто = брутто − комиссия; списание с депозита = брутто + комиссия."""
    net = _q(gross - fee)
    delta = _q(-(gross + fee))
    return net, delta
