"""Учёт успешного платежа в активном марафоне."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from bot.payments.currency_converter import resolve_payment_datetime_for_rates
from bot.services.donation_marathon_fx import convert_amount_to_marathon_goal
from bot.services.donation_marathon_progress import thank_you_remaining_html

logger = logging.getLogger(__name__)


async def attribute_payment_to_marathon(
    user_storage,
    payment: Dict[str, Any],
    *,
    rub_amount: Optional[float],
    currency_converter=None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Пишет contribution (сумма в валюте цели + FX-поля) и при необходимости закрывает марафон.
    Таблица ``payments`` не меняется.
    Возвращает (marathon_row | None, thank_you_html | None).
    """
    marathon_id = payment.get("marathon_id")
    if not marathon_id:
        return None, None

    marathon = await user_storage.get_donation_marathon(int(marathon_id))
    if not marathon:
        return None, None

    goal_cur = str(marathon.get("goal_currency") or "USD").upper()
    pay_cur = str(payment.get("currency") or "RUB").upper()
    pay_amt = float(payment.get("amount") or 0)

    if pay_cur == "RUB" and not marathon.get("accept_rub"):
        logger.info("Платёж %s RUB пропущен: марафон не принимает RUB", payment.get("id"))
        return None, None
    if pay_cur == "USD" and not marathon.get("accept_usd"):
        logger.info("Платёж %s USD пропущен: марафон не принимает USD", payment.get("id"))
        return None, None

    resolved_rub = rub_amount
    if resolved_rub is None and payment.get("amount_rub") is not None:
        resolved_rub = float(payment["amount_rub"])

    when = resolve_payment_datetime_for_rates(payment)
    fx = await convert_amount_to_marathon_goal(
        amount=pay_amt,
        currency=pay_cur,
        goal_currency=goal_cur,
        amount_rub=resolved_rub,
        currency_converter=currency_converter,
        rate_date=when.date(),
    )

    if fx is None or fx.amount_goal <= 0:
        logger.error(
            "Не удалось перевести платёж %s (%s %s) в валюту марафона %s",
            payment.get("id"),
            pay_amt,
            pay_cur,
            goal_cur,
        )
        return marathon if marathon.get("status") == "active" else None, None

    await user_storage.add_marathon_contribution(
        marathon_id=int(marathon_id),
        user_id=int(payment["user_id"]),
        amount_goal=float(fx.amount_goal),
        amount_original=float(fx.amount_original),
        currency_original=fx.currency_original,
        payment_id=int(payment["id"]),
        source="payment",
        goal_currency=fx.goal_currency,
        amount_rub=fx.amount_rub,
        rub_per_goal_unit=fx.rub_per_goal_unit,
        rate_original_to_goal=fx.rate_original_to_goal,
        fx_source=fx.fx_source,
    )

    raised = await user_storage.get_marathon_raised_amount(int(marathon_id))
    thank = thank_you_remaining_html(marathon, raised=raised)

    if marathon.get("status") == "active" and raised + 1e-9 >= float(marathon["goal_amount"]):
        await user_storage.close_donation_marathon(
            int(marathon_id),
            close_reason="goal_reached",
            status="completed",
        )
        marathon = await user_storage.get_donation_marathon(int(marathon_id)) or marathon
        thank = thank_you_remaining_html(marathon, raised=raised)

    return marathon, thank
