"""Учёт успешного платежа в марафоне + ретроспективный бэкфилл."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from bot.payments.currency_converter import resolve_payment_datetime_for_rates
from bot.services.donation_marathon_fx import (
    convert_amount_to_marathon_goal,
    payment_in_marathon_window,
)
from bot.services.donation_marathon_progress import thank_you_remaining_html

logger = logging.getLogger(__name__)


async def attribute_payment_to_marathon(
    user_storage,
    payment: Dict[str, Any],
    *,
    rub_amount: Optional[float] = None,
    currency_converter=None,
    marathon: Optional[Dict[str, Any]] = None,
    allow_autoclose: bool = True,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Записывает взнос в ``donation_marathon_contributions``.

    Пока марафон **active** — любой успешный донат (``order_id IS NULL``) в его период
    учитывается, не только платежи через кнопку марафона.
    ``payments`` не меняется.
    """
    if payment.get("order_id") is not None:
        return None, None

    explicit = marathon is not None
    if marathon is None:
        marathon = await user_storage.get_active_donation_marathon()
        if not marathon or marathon.get("status") != "active":
            return None, None

    marathon_id = int(marathon["id"])

    if not payment_in_marathon_window(payment, marathon):
        return None, None

    payment_id = payment.get("id")
    if payment_id is not None:
        existing = await user_storage.get_marathon_contribution_by_payment_id(int(payment_id))
        if existing:
            return marathon, None

    goal_cur = str(marathon.get("goal_currency") or "USD").upper()
    pay_cur = str(payment.get("currency") or "RUB").upper()
    pay_amt = float(payment.get("amount") or 0)

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
        return marathon if marathon.get("status") == "active" or explicit else None, None

    row = await user_storage.add_marathon_contribution(
        marathon_id=marathon_id,
        user_id=int(payment["user_id"]),
        amount_goal=float(fx.amount_goal),
        amount_original=float(fx.amount_original),
        currency_original=fx.currency_original,
        payment_id=int(payment_id) if payment_id is not None else None,
        source="payment",
        goal_currency=fx.goal_currency,
        amount_rub=fx.amount_rub,
        rub_per_goal_unit=fx.rub_per_goal_unit,
        rate_original_to_goal=fx.rate_original_to_goal,
        fx_source=fx.fx_source,
    )
    if not row:
        return marathon, None

    raised = await user_storage.get_marathon_raised_amount(marathon_id)
    thank = thank_you_remaining_html(marathon, raised=raised)

    if marathon.get("status") == "active" and allow_autoclose and raised + 1e-9 >= float(
        marathon["goal_amount"]
    ):
        await user_storage.close_donation_marathon(
            marathon_id,
            close_reason="goal_reached",
            status="completed",
        )
        marathon = await user_storage.get_donation_marathon(marathon_id) or marathon
        thank = thank_you_remaining_html(marathon, raised=raised)

    return marathon, thank


async def backfill_marathon_contributions(
    user_storage,
    marathon_id: int,
    *,
    currency_converter=None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Ретроспективно учесть все донаты за период марафона."""
    marathon = await user_storage.get_donation_marathon(marathon_id)
    if not marathon:
        raise ValueError(f"Марафон id={marathon_id} не найден")

    payments = await user_storage.list_standalone_payments_for_marathon_backfill(marathon)
    stats: Dict[str, Any] = {
        "marathon_id": marathon_id,
        "marathon_name": marathon.get("name"),
        "payments_found": len(payments),
        "added": 0,
        "skipped": 0,
        "errors": 0,
        "dry_run": dry_run,
    }

    for payment in payments:
        pid = payment.get("id")
        if pid is not None:
            existing = await user_storage.get_marathon_contribution_by_payment_id(int(pid))
            if existing:
                stats["skipped"] += 1
                continue

        if not payment_in_marathon_window(payment, marathon):
            stats["skipped"] += 1
            continue

        if dry_run:
            stats["added"] += 1
            continue

        try:
            rub_amount = (
                float(payment["amount_rub"])
                if payment.get("amount_rub") is not None
                else None
            )
            _, _ = await attribute_payment_to_marathon(
                user_storage,
                payment,
                rub_amount=rub_amount,
                currency_converter=currency_converter,
                marathon=marathon,
                allow_autoclose=False,
            )
            if pid is not None:
                check = await user_storage.get_marathon_contribution_by_payment_id(int(pid))
                if check:
                    stats["added"] += 1
                else:
                    stats["errors"] += 1
            else:
                stats["errors"] += 1
        except Exception as e:
            logger.error("backfill payment id=%s: %s", pid, e, exc_info=True)
            stats["errors"] += 1

    stats["raised_after"] = await user_storage.get_marathon_raised_amount(marathon_id)
    stats["donors_after"] = await user_storage.get_marathon_donors_count(marathon_id)
    goal = float(marathon.get("goal_amount") or 0)
    if (
        marathon.get("status") == "active"
        and not dry_run
        and stats["raised_after"] + 1e-9 >= goal
    ):
        await user_storage.close_donation_marathon(
            marathon_id,
            close_reason="goal_reached",
            status="completed",
        )
        stats["auto_closed"] = True
    else:
        stats["auto_closed"] = False

    return stats
