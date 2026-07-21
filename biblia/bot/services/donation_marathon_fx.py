"""Конвертация взносов марафона в валюту цели + поля FX для contributions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Optional


@dataclass
class MarathonFxResult:
    amount_goal: float
    amount_original: float
    currency_original: str
    goal_currency: str
    amount_rub: Optional[float]
    rub_per_goal_unit: Optional[float]
    rate_original_to_goal: Optional[float]
    fx_source: str


def _norm_currency(code: str) -> str:
    cur = (code or "").strip().upper()
    if cur in ("USDT", "USDTTRC20", "USDT_TRC20"):
        return "USDT"
    return cur


def _effective_pay_currency(currency: str) -> str:
    """USDT всегда считаем как USD для цели/кросс-курса."""
    cur = _norm_currency(currency)
    if cur == "USDT":
        return "USD"
    return cur


async def convert_amount_to_marathon_goal(
    *,
    amount: float,
    currency: str,
    goal_currency: str,
    amount_rub: Optional[float] = None,
    currency_converter=None,
    rate_date: Optional[date] = None,
    fx_source_hint: Optional[str] = None,
) -> Optional[MarathonFxResult]:
    """
    Переводит сумму в валюту цели марафона.

    - ``payments`` не меняется: ``amount_rub`` только копируем/используем как pivot.
    - USDT = USD (1:1).
    - RUB ↔ USD/EUR — через ЦБ (рублей за 1 единицу валюты цели).
    """
    amt = float(amount)
    if amt <= 0:
        return None

    orig = _norm_currency(currency)
    goal = _norm_currency(goal_currency)
    if goal == "USDT":
        goal = "USD"
    pay_eff = _effective_pay_currency(orig)

    # Исходная валюта = цель (или USDT→USD при цели USD)
    if pay_eff == goal or (orig == "USDT" and goal == "USD"):
        source = "usdt_eq_usd" if orig == "USDT" else (fx_source_hint or "same_currency")
        rub = amount_rub
        if rub is None and pay_eff == "RUB":
            rub = amt
        return MarathonFxResult(
            amount_goal=amt,
            amount_original=amt,
            currency_original=orig,
            goal_currency=goal,
            amount_rub=float(rub) if rub is not None else None,
            rub_per_goal_unit=1.0 if goal == "RUB" else None,
            rate_original_to_goal=1.0,
            fx_source=source,
        )

    # Нужен рублёвый pivot
    rub = amount_rub
    if rub is None and pay_eff == "RUB":
        rub = amt

    day = rate_date or date.today()
    rub_per_goal: Optional[float] = None
    rub_per_pay: Optional[float] = None

    if currency_converter is not None:
        if goal != "RUB":
            rub_per_goal = await currency_converter.get_rate_to_rub(goal, day)
        else:
            rub_per_goal = 1.0
        if pay_eff != "RUB" and rub is None:
            rub_per_pay = await currency_converter.get_rate_to_rub(pay_eff, day)
            if rub_per_pay and rub_per_pay > 0:
                rub = amt * rub_per_pay

    if goal == "RUB":
        if rub is None:
            return None
        rate_otg = (float(rub) / amt) if amt else None
        return MarathonFxResult(
            amount_goal=float(rub),
            amount_original=amt,
            currency_original=orig,
            goal_currency=goal,
            amount_rub=float(rub),
            rub_per_goal_unit=1.0,
            rate_original_to_goal=rate_otg,
            fx_source=fx_source_hint or "cbr",
        )

    # Цель не RUB: amount_goal = amount_rub / rub_per_goal_unit
    if rub is None or rub_per_goal is None or rub_per_goal <= 0:
        return None

    amount_goal = float(rub) / float(rub_per_goal)
    rate_otg = amount_goal / amt if amt else None
    source = "usdt_eq_usd" if orig == "USDT" else (fx_source_hint or "cbr")
    return MarathonFxResult(
        amount_goal=amount_goal,
        amount_original=amt,
        currency_original=orig,
        goal_currency=goal,
        amount_rub=float(rub),
        rub_per_goal_unit=float(rub_per_goal),
        rate_original_to_goal=rate_otg,
        fx_source=source,
    )


def payment_amount_in_goal_currency(
    *,
    payment_amount: float,
    payment_currency: str,
    goal_currency: str,
    amount_rub: Optional[float],
    rub_per_goal_unit: Optional[float],
) -> Optional[float]:
    """Совместимость со старым вызовом (без async)."""
    pay = _effective_pay_currency(payment_currency)
    goal = _norm_currency(goal_currency)
    if goal == "USDT":
        goal = "USD"
    amt = float(payment_amount)
    if pay == goal:
        return amt
    rub = amount_rub
    if rub is None and pay == "RUB":
        rub = amt
    if rub is None:
        return None
    if goal == "RUB":
        return float(rub)
    if rub_per_goal_unit is None or rub_per_goal_unit <= 0:
        return None
    return float(rub) / float(rub_per_goal_unit)
