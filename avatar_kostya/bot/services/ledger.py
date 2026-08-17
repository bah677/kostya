"""Расчёт сумм журнала: брутто / комиссия / нетто и влияние на депозит."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date, datetime, timedelta
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

MONEY_Q = Decimal("0.00000001")
DEFAULT_CURRENCY = "USDT"
_MSK = ZoneInfo("Europe/Moscow")


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
    return f"{format_amount(amount)} {currency}"


def format_amount(amount) -> str:
    d = _q(Decimal(str(amount or 0)))
    s = format(d, "f").rstrip("0").rstrip(".")
    if s in ("", "-"):
        s = "0"
    return s


def deposit_amounts(gross: Decimal, fee: Decimal) -> Tuple[Decimal, Decimal]:
    """Возвращает (нетто, delta остатка). Нетто = брутто − комиссия."""
    net = _q(gross - fee)
    return net, net


def expense_amounts(gross: Decimal, fee: Decimal) -> Tuple[Decimal, Decimal]:
    """Нетто = брутто − комиссия; списание с депозита = брутто + комиссия."""
    net = _q(gross - fee)
    delta = _q(-(gross + fee))
    return net, delta


def today_msk() -> date:
    return datetime.now(_MSK).date()


def parse_op_date(text: str, *, today: Optional[date] = None) -> Optional[date]:
    """сегодня / вчера / ДД.ММ / ДД.ММ.ГГГГ / ГГГГ-ММ-ДД."""
    raw = (text or "").strip().lower()
    day = today or today_msk()
    if not raw or raw in ("-", "сегодня", "today"):
        return day
    if raw in ("вчера", "yesterday"):
        return day - timedelta(days=1)
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.strptime(f"{raw}.{day.year}", "%d.%m.%Y").date()
    except ValueError:
        return None
