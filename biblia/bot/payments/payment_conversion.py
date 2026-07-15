"""Пересчёт суммы платежа в RUB по курсу ЦБ на дату операции."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from bot.payments.currency_converter import (
    CurrencyConverterService,
    resolve_payment_datetime_for_rates,
)


async def compute_payment_rub_conversion(
    currency_converter: Optional[CurrencyConverterService],
    amount: float,
    currency: str,
    payment_date: datetime,
) -> Tuple[Optional[float], Optional[float]]:
    """
    Возвращает (amount_rub, exchange_rate).

    ``exchange_rate`` — рублей за 1 единицу валюты платежа (для RUB = 1.0).
  При ошибке FX для не-RUB — (None, None).
    """
    amount_f = float(amount or 0)
    currency_u = (currency or "RUB").strip().upper()

    if currency_u == "RUB":
        return amount_f, 1.0

    if currency_converter is None:
        return None, None

    rub_amount = await currency_converter.convert_payment_amount(
        amount_f,
        currency_u,
        payment_date,
    )
    if rub_amount is None:
        return None, None

    rate = rub_amount / amount_f if amount_f else None
    return rub_amount, rate


async def compute_payment_rub_from_row(
    currency_converter: Optional[CurrencyConverterService],
    payment: Dict[str, Any],
) -> Tuple[Optional[float], Optional[float]]:
    pay_dt = resolve_payment_datetime_for_rates(payment)
    return await compute_payment_rub_conversion(
        currency_converter,
        float(payment.get("amount") or 0),
        payment.get("currency") or "RUB",
        pay_dt,
    )
