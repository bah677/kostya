"""Ожидаемый приход брутто: 35% донатов Biblia в USD, журнал по дням МСК."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

import asyncpg

from bot.services.ledger import MONEY_Q, today_msk

logger = logging.getLogger(__name__)

_MSK = ZoneInfo("Europe/Moscow")
JOURNAL_START = date(2026, 8, 16)
SHARE_PCT = Decimal("0.35")
SOURCE = "biblia_bot"


def _q(value: Decimal) -> Decimal:
    return value.quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def msk_day_bounds(day: date) -> Tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=_MSK)
    return start, start + timedelta(days=1)


def completed_day_for_report(*, now: Optional[date] = None) -> date:
    """Последний полностью закрытый календарный день МСК (вчера)."""
    return (now or today_msk()) - timedelta(days=1)


class LedgerExpectedIncomeService:
    def __init__(
        self,
        user_storage,
        *,
        currency_converter,
        biblia_dsn: str,
    ) -> None:
        self.user_storage = user_storage
        self.currency_converter = currency_converter
        self.biblia_dsn = (biblia_dsn or "").strip()
        self._pool: Optional[asyncpg.Pool] = None

    async def start(self) -> None:
        await self.user_storage.ensure_ledger_expected_income_schema()
        if not self.biblia_dsn:
            logger.warning("ledger expected income: нет DSN Biblia — журнал не заполняется")
            return
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self.biblia_dsn, min_size=1, max_size=2)
        await self.sync_through(completed_day_for_report())

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def sync_through(
        self,
        end_day: date,
        *,
        force_from: Optional[date] = None,
    ) -> int:
        """Дописать дни с JOURNAL_START по end_day включительно. Возвращает число upsert."""
        if end_day < JOURNAL_START:
            return 0
        if self._pool is None:
            logger.warning("ledger expected income: pool не готов")
            return 0
        existing = set(await self.user_storage.list_ledger_expected_income_days())
        n = 0
        day = JOURNAL_START
        while day <= end_day:
            need = force_from is not None and day >= force_from
            if need or day not in existing:
                await self._upsert_day(day)
                n += 1
            day += timedelta(days=1)
        await self.user_storage.recompute_ledger_expected_income_cumulative()
        return n

    async def get_for_report(self) -> Optional[Dict[str, Any]]:
        """Строка журнала на конец последнего закрытого дня (или ближайший ≤)."""
        await self.user_storage.ensure_ledger_expected_income_schema()
        target = completed_day_for_report()
        if target < JOURNAL_START:
            return None
        row = await self.user_storage.get_ledger_expected_income_as_of(target)
        if row is None and self._pool is not None:
            await self.sync_through(target)
            row = await self.user_storage.get_ledger_expected_income_as_of(target)
        return row

    async def _upsert_day(self, day: date) -> None:
        rub, count = await self._sum_donations_rub(day)
        rate = await self.currency_converter.get_rate_to_rub("USD", day)
        if rate is None or rate <= 0:
            logger.error("ledger expected income: нет курса USD на %s", day)
            donations_usd = Decimal("0")
            rate_d: Optional[Decimal] = None
        else:
            rate_d = _q(Decimal(str(rate)))
            donations_usd = _q(rub / rate_d) if rub else Decimal("0")
        day_share = _q(donations_usd * SHARE_PCT)
        await self.user_storage.upsert_ledger_expected_income_day(
            day=day,
            donations_count=count,
            donations_rub=rub,
            usd_rub_rate=rate_d,
            donations_usd=donations_usd,
            share_pct=SHARE_PCT,
            day_share_usd=day_share,
            source=SOURCE,
        )
        logger.info(
            "ledger expected income day=%s rub=%s usd=%s share=%s n=%s",
            day,
            rub,
            donations_usd,
            day_share,
            count,
        )

    async def _sum_donations_rub(self, day: date) -> Tuple[Decimal, int]:
        assert self._pool is not None
        start, end = msk_day_bounds(day)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COALESCE(SUM(amount_rub), 0) AS rub,
                       COUNT(*)::int AS n
                  FROM payments
                 WHERE status = 'succeeded'
                   AND order_id IS NULL
                   AND amount_rub IS NOT NULL
                   AND created_at >= $1
                   AND created_at < $2
                """,
                start,
                end,
            )
        rub = _q(Decimal(str((row and row["rub"]) or 0)))
        count = int((row and row["n"]) or 0)
        return rub, count
