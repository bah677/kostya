# bot/services/manual_donation.py
"""Универсальный ручной учёт доната админом → payments + опции марафон/пул голоса."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from bot.payments.currency_converter import CurrencyConverterService
from bot.services.donation_marathon_fx import convert_amount_to_marathon_goal
from bot.services.prayer_voice_funding import PrayerVoiceFundingService
from bot.services.prayer_voice_quota import (
    msk_now,
    previous_quota_day,
    quota_day_for,
    quota_window,
)

logger = logging.getLogger(__name__)

PURPOSE_MANUAL = "manual_admin"

# Период пула: куда класть completed_at относительно окна квоты.
POOL_PERIOD_CURRENT = "current"
POOL_PERIOD_NEXT = "next"


def normalize_currency(code: str) -> str:
    """USDT и аналоги → USD: у ЦБ нет USDT, крипту учитываем как доллары."""
    cur = (code or "").strip().upper()
    if cur in ("USDT", "USDTTRC20", "USDT_TRC20", "USDT-TRC20", "USDC"):
        return "USD"
    return cur


def fx_currency_for_cbr(currency: str) -> str:
    return normalize_currency(currency)


@dataclass
class ManualDonationResult:
    payment: Dict[str, Any]
    marathon_contribution: Optional[Dict[str, Any]]
    marathon_closed: bool
    pool_period: Optional[str]
    period_info: Optional[Dict[str, Any]]
    next_slots: Optional[int]


async def record_manual_donation(
    user_storage,
    *,
    amount: float,
    currency: str,
    provider: str,
    user_id: int,
    note: str,
    created_by: int,
    count_in_marathon: bool,
    count_in_voice_pool: bool,
    pool_period: Optional[str],
    currency_converter: Optional[CurrencyConverterService] = None,
) -> ManualDonationResult:
    """
    Создаёт succeeded-платёж и по флагам:
    - пишет взнос в активный марафон (без проверки окна по времени);
    - кладёт completed_at в нужное окно пула и при необходимости пересчитывает текущий период.
    """
    if amount <= 0:
        raise ValueError("amount must be > 0")

    cur = normalize_currency(currency)
    if not cur or len(cur) > 12:
        raise ValueError("bad currency")

    conv = currency_converter or CurrencyConverterService()
    now = msk_now()
    pool_period_n = (pool_period or "").strip().lower() or None
    if count_in_voice_pool and pool_period_n not in (
        POOL_PERIOD_CURRENT,
        POOL_PERIOD_NEXT,
    ):
        raise ValueError("pool_period must be current|next when counting in pool")

    completed_at = _resolve_completed_at(
        now=now,
        count_in_voice_pool=count_in_voice_pool,
        pool_period=pool_period_n,
    )

    fx_cur = fx_currency_for_cbr(cur)
    rub = await conv.convert_payment_amount(amount, fx_cur, completed_at)
    if rub is None:
        raise RuntimeError(f"Нет курса ЦБ для {fx_cur}")

    rate: Optional[float]
    if fx_cur == "RUB":
        rate = 1.0
    else:
        rate = await conv.get_rate_to_rub(fx_cur, completed_at.date())

    marathon = None
    marathon_id = None
    if count_in_marathon:
        marathon = await user_storage.get_active_donation_marathon()
        if not marathon or marathon.get("status") != "active":
            raise RuntimeError("Нет активного марафона")
        marathon_id = int(marathon["id"])

    meta = {
        "manual": True,
        "created_by": int(created_by),
        "note": (note or "").strip()[:500] or None,
        "pool_period": pool_period_n if count_in_voice_pool else None,
        "count_in_marathon": bool(count_in_marathon),
    }
    if int(user_id) > 0:
        try:
            urow = await user_storage.get_user(int(user_id))
            if urow:
                meta["username"] = urow.get("username")
                meta["first_name"] = urow.get("first_name")
                meta["last_name"] = urow.get("last_name")
        except Exception:
            logger.debug("manual donation: enrich user meta failed", exc_info=True)
    else:
        meta["first_name"] = "Неизвестный донор"
    provider_pid = f"manual:{uuid.uuid4().hex[:16]}"
    if note.strip():
        # Короткий хвост в provider id удобен в отчётах; уникальность — uuid.
        provider_pid = f"manual:{uuid.uuid4().hex[:12]}:{note.strip()[:40]}"

    payment = await user_storage.create_manual_succeeded_payment(
        user_id=int(user_id),
        amount=float(amount),
        currency=cur,
        provider=(provider or "manual").strip().lower()[:64],
        provider_payment_id=provider_pid[:200],
        amount_rub=float(rub),
        exchange_rate=float(rate) if rate is not None else None,
        completed_at=completed_at,
        marathon_id=marathon_id,
        purpose=PURPOSE_MANUAL,
        counts_for_voice_pool=bool(count_in_voice_pool),
        user_telegram_data=json.dumps(meta, ensure_ascii=False),
    )
    if not payment:
        raise RuntimeError("Не удалось создать платёж")

    contrib = None
    marathon_closed = False
    if count_in_marathon and marathon is not None:
        contrib, marathon_closed = await _attribute_to_marathon(
            user_storage,
            payment=payment,
            marathon=marathon,
            currency_converter=conv,
            note=note,
            created_by=created_by,
        )

    period_info: Optional[Dict[str, Any]] = None
    next_slots: Optional[int] = None
    funding = PrayerVoiceFundingService(user_storage, currency_converter=conv)
    if count_in_voice_pool:
        if pool_period_n == POOL_PERIOD_CURRENT:
            day = quota_day_for(now)
            info = await funding.recalculate_locked_period(day)
            period_info = {
                "quota_day": str(info.quota_day),
                "limit_slots": info.limit_slots,
                "computed_slots": info.computed_slots,
                "used": info.used,
                "remaining": info.remaining,
                "revenue_rub": info.revenue_rub,
                "revenue_usd": info.revenue_usd,
            }
        else:
            next_slots = await funding.indicative_next_slots(now=now)

    return ManualDonationResult(
        payment=payment,
        marathon_contribution=contrib,
        marathon_closed=marathon_closed,
        pool_period=pool_period_n if count_in_voice_pool else None,
        period_info=period_info,
        next_slots=next_slots,
    )


def _resolve_completed_at(
    *,
    now: datetime,
    count_in_voice_pool: bool,
    pool_period: Optional[str],
) -> datetime:
    if not count_in_voice_pool:
        return now
    if pool_period == POOL_PERIOD_NEXT:
        # Окно текущего quota_day → кормит лимит «на завтра».
        start, end = quota_window(quota_day_for(now))
        if start <= now < end:
            return now
        return start + timedelta(hours=1)
    # current: окно предыдущего quota_day → кормит уже зафиксированный сегодняшний лимит.
    prev = previous_quota_day(quota_day_for(now))
    start, end = quota_window(prev)
    # Середина окна — гарантированно внутри [start, end).
    mid = start + (end - start) / 2
    return mid


async def _attribute_to_marathon(
    user_storage,
    *,
    payment: Dict[str, Any],
    marathon: Dict[str, Any],
    currency_converter: CurrencyConverterService,
    note: str,
    created_by: int,
) -> tuple[Optional[Dict[str, Any]], bool]:
    """Явный учёт в марафоне (окно по времени не проверяем — админ уже выбрал)."""
    mid = int(marathon["id"])
    pid = int(payment["id"])
    existing = await user_storage.get_marathon_contribution_by_payment_id(pid)
    if existing:
        return existing, False

    goal_cur = str(marathon.get("goal_currency") or "USD").upper()
    pay_cur = normalize_currency(str(payment.get("currency") or "RUB"))
    pay_amt = float(payment.get("amount") or 0)
    rub = (
        float(payment["amount_rub"])
        if payment.get("amount_rub") is not None
        else None
    )
    when = payment.get("completed_at")
    rate_day = when.date() if isinstance(when, datetime) else msk_now().date()

    fx = await convert_amount_to_marathon_goal(
        amount=pay_amt,
        currency=pay_cur,
        goal_currency=goal_cur,
        amount_rub=rub,
        currency_converter=currency_converter,
        rate_date=rate_day,
        fx_source_hint="manual_admin",
    )
    if fx is None or fx.amount_goal <= 0:
        raise RuntimeError("Не удалось перевести сумму в валюту цели марафона")

    row = await user_storage.add_marathon_contribution(
        marathon_id=mid,
        user_id=int(payment["user_id"]),
        amount_goal=float(fx.amount_goal),
        amount_original=float(fx.amount_original),
        currency_original=fx.currency_original,
        payment_id=pid,
        source="manual",
        note=(note or "").strip() or None,
        created_by=int(created_by),
        goal_currency=fx.goal_currency,
        amount_rub=fx.amount_rub,
        rub_per_goal_unit=fx.rub_per_goal_unit,
        rate_original_to_goal=fx.rate_original_to_goal,
        fx_source=fx.fx_source,
    )
    if not row:
        raise RuntimeError("Не удалось записать взнос марафона")

    closed = False
    raised = await user_storage.get_marathon_raised_amount(mid)
    if raised + 1e-9 >= float(marathon["goal_amount"]):
        await user_storage.close_donation_marathon(
            mid,
            close_reason="goal_reached",
            status="completed",
        )
        closed = True
    return row, closed
