# bot/services/prayer_voice_funding.py
"""Лимит голоса от донатов: фиксация периода в 08:00 МСК + индикатив на завтра.

Неиспользованные слоты переносятся на следующие квотные сутки (накопительный пул).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Optional

from bot.services.prayer_voice_quota import (
    apply_min_floor,
    previous_quota_day,
    quota_day_for,
    quota_window,
    slots_from_donation_usd,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrayerVoicePeriodInfo:
    quota_day: date
    limit_slots: int
    computed_slots: int
    min_floor: int
    revenue_usd: float
    revenue_rub: float
    used: int
    carryover_slots: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit_slots - self.used)


class PrayerVoiceFundingService:
    def __init__(self, user_storage, currency_converter=None) -> None:
        self.user_storage = user_storage
        self.currency_converter = currency_converter

    async def ensure_current_period(
        self, *, now: Optional[datetime] = None
    ) -> PrayerVoicePeriodInfo:
        day = quota_day_for(now)
        return await self.ensure_period(day)

    async def _carryover_from_previous(self, day: date) -> int:
        """Сколько слотов осталось вчера и переносится на ``day``."""
        prev = previous_quota_day(day)
        prev_row = await self.user_storage.get_prayer_voice_period(prev)
        if not prev_row:
            return 0
        used = await self.user_storage.count_prayer_voice_quota_used(quota_day=prev)
        limit = int(prev_row.get("limit_slots") or 0)
        return max(0, limit - int(used or 0))

    async def ensure_period(self, day: date) -> PrayerVoicePeriodInfo:
        await self.user_storage.ensure_prayer_voice_quota_schema()
        existing = await self.user_storage.get_prayer_voice_period(day)
        if existing:
            used = await self.user_storage.count_prayer_voice_quota_used(quota_day=day)
            return self._row_to_info(existing, used=used)

        # Доходы за предыдущие 24ч (окно прошлого quota_day) → лимит на ``day``.
        prev = previous_quota_day(day)
        start, end = quota_window(prev)
        rub = await self.user_storage.sum_succeeded_payments_rub(start, end)
        usd = await self._rub_to_usd(rub, on_date=end)
        computed = slots_from_donation_usd(usd)
        min_floor = await self.user_storage.get_prayer_voice_min_limit()
        carryover = await self._carryover_from_previous(day)
        base = apply_min_floor(computed, min_floor)
        limit = base + carryover
        row = await self.user_storage.insert_prayer_voice_period(
            quota_day=day,
            limit_slots=limit,
            computed_slots=computed,
            min_floor=min_floor,
            revenue_usd=usd,
            revenue_rub=rub,
            carryover_slots=carryover,
        )
        used = await self.user_storage.count_prayer_voice_quota_used(quota_day=day)
        logger.info(
            "prayer voice period locked day=%s limit=%s computed=%s min=%s "
            "carryover=%s rub=%.2f usd=%.2f",
            day,
            limit,
            computed,
            min_floor,
            carryover,
            rub,
            usd,
        )
        return self._row_to_info(
            row
            or {
                "quota_day": day,
                "limit_slots": limit,
                "computed_slots": computed,
                "min_floor": min_floor,
                "revenue_usd": usd,
                "revenue_rub": rub,
                "carryover_slots": carryover,
            },
            used=used,
        )

    async def recalculate_locked_period(
        self, day: date
    ) -> Optional[PrayerVoicePeriodInfo]:
        """
        Пересчитать уже зафиксированный период по донатам окна предыдущих суток.
        Перенос с вчера сохраняем; лимит не опускаем ниже уже использованных слотов.
        """
        existing = await self.user_storage.get_prayer_voice_period(day)
        if not existing:
            return await self.ensure_period(day)

        prev = previous_quota_day(day)
        start, end = quota_window(prev)
        rub = await self.user_storage.sum_succeeded_payments_rub(start, end)
        usd = await self._rub_to_usd(rub, on_date=end)
        computed = slots_from_donation_usd(usd)
        min_floor = await self.user_storage.get_prayer_voice_min_limit()
        # Перенос фиксируется при создании суток; при пересчёте не трогаем.
        carryover = int(existing.get("carryover_slots") or 0)
        base = apply_min_floor(computed, min_floor)
        limit = base + carryover
        used = await self.user_storage.count_prayer_voice_quota_used(quota_day=day)
        limit = max(limit, used)
        row = await self.user_storage.update_prayer_voice_period_totals(
            quota_day=day,
            limit_slots=limit,
            computed_slots=computed,
            min_floor=min_floor,
            revenue_usd=usd,
            revenue_rub=rub,
            carryover_slots=carryover,
        )
        logger.info(
            "prayer voice period recalculated day=%s limit=%s computed=%s "
            "carryover=%s used=%s rub=%.2f usd=%.2f",
            day,
            limit,
            computed,
            carryover,
            used,
            rub,
            usd,
        )
        return self._row_to_info(row or existing, used=used)

    async def indicative_next_slots(
        self, *, now: Optional[datetime] = None
    ) -> int:
        """
        Индикатив на завтра: max(расчёт от донатов текущего окна, минимум из /adm)
        + остаток сегодняшнего пула (перенос).
        """
        day = quota_day_for(now)
        start, end = quota_window(day)
        rub = await self.user_storage.sum_succeeded_payments_rub(start, end)
        from bot.services.prayer_voice_quota import msk_now

        usd = await self._rub_to_usd(rub, on_date=msk_now().date())
        computed = slots_from_donation_usd(usd)
        min_floor = await self.user_storage.get_prayer_voice_min_limit()
        base = apply_min_floor(computed, min_floor)
        # Остаток сегодня → перенос на завтра.
        try:
            today = await self.ensure_current_period(now=now)
            carry_preview = int(today.remaining)
        except Exception:
            carry_preview = 0
        return base + carry_preview

    async def get_status(self, *, now: Optional[datetime] = None) -> Dict[str, Any]:
        period = await self.ensure_current_period(now=now)
        next_slots = await self.indicative_next_slots(now=now)
        min_floor = await self.user_storage.get_prayer_voice_min_limit()
        per_user = await self.user_storage.get_prayer_voice_per_user_daily()
        return {
            "quota_day": period.quota_day,
            "limit": period.limit_slots,
            "used": period.used,
            "remaining": period.remaining,
            "computed_slots": period.computed_slots,
            "carryover_slots": period.carryover_slots,
            "min_floor": min_floor,
            "per_user_daily": per_user,
            "revenue_usd": period.revenue_usd,
            "revenue_rub": period.revenue_rub,
            "next_slots": next_slots,
        }

    async def try_acquire_access(
        self, user_id: int
    ) -> Optional[Dict[str, Any]]:
        period = await self.ensure_current_period()
        per_user = await self.user_storage.get_prayer_voice_per_user_daily()
        return await self.user_storage.try_acquire_prayer_voice_access(
            user_id,
            limit_slots=period.limit_slots,
            quota_day=period.quota_day,
            per_user_daily=per_user,
        )

    async def _rub_to_usd(
        self, rub: float, *, on_date: Optional[date]
    ) -> float:
        rub = float(rub or 0.0)
        if rub <= 0:
            return 0.0
        conv = self.currency_converter
        if conv is None:
            logger.warning("prayer voice funding: no FX converter, usd≈0")
            return 0.0
        from bot.services.prayer_voice_quota import msk_now

        day = on_date or msk_now().date()
        try:
            rate = await conv.get_rate_to_rub("USD", day)
        except Exception as e:
            logger.error("prayer voice funding USD rate: %s", e)
            return 0.0
        if not rate or rate <= 0:
            return 0.0
        return rub / float(rate)

    @staticmethod
    def _row_to_info(row: Dict[str, Any], *, used: int) -> PrayerVoicePeriodInfo:
        return PrayerVoicePeriodInfo(
            quota_day=row["quota_day"],
            limit_slots=int(row["limit_slots"]),
            computed_slots=int(row.get("computed_slots") or 0),
            min_floor=int(row.get("min_floor") or 0),
            revenue_usd=float(row.get("revenue_usd") or 0),
            revenue_rub=float(row.get("revenue_rub") or 0),
            used=int(used or 0),
            carryover_slots=int(row.get("carryover_slots") or 0),
        )
