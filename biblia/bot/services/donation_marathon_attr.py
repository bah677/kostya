"""Учёт успешного платежа в активном марафоне."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from bot.payments.currency_converter import resolve_payment_datetime_for_rates
from bot.services.donation_marathon_progress import (
    payment_amount_in_goal_currency,
    thank_you_remaining_html,
)

logger = logging.getLogger(__name__)


async def attribute_payment_to_marathon(
    user_storage,
    payment: Dict[str, Any],
    *,
    rub_amount: Optional[float],
    currency_converter=None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Пишет contribution и при необходимости закрывает марафон.
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

    rub_per_goal: Optional[float] = None
    if goal_cur != "RUB" and pay_cur != goal_cur:
        if currency_converter is not None:
            try:
                when = resolve_payment_datetime_for_rates(payment)
                rub_per_goal = await currency_converter.get_rate_to_rub(goal_cur, when.date())
            except Exception as e:
                logger.warning("marathon FX for goal %s: %s", goal_cur, e)

    amount_goal = payment_amount_in_goal_currency(
        payment_amount=pay_amt,
        payment_currency=pay_cur,
        goal_currency=goal_cur,
        amount_rub=resolved_rub,
        rub_per_goal_unit=rub_per_goal,
    )

    if amount_goal is None or amount_goal <= 0:
        logger.error(
            "Не удалось перевести платёж %s в валюту марафона %s",
            payment.get("id"),
            goal_cur,
        )
        return marathon if marathon.get("status") == "active" else None, None

    await user_storage.add_marathon_contribution(
        marathon_id=int(marathon_id),
        user_id=int(payment["user_id"]),
        amount_goal=float(amount_goal),
        amount_original=pay_amt,
        currency_original=pay_cur,
        payment_id=int(payment["id"]),
        source="payment",
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
