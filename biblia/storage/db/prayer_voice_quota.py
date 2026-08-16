"""
Mixin: лимит бесплатных голосовых молитв и разблокировка донатом.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, Optional

from bot.services.prayer_voice_quota import (
    DEFAULT_MIN_LIMIT,
    DEFAULT_PER_USER_DAILY,
    SETTING_KEY_MIN_LIMIT,
    SETTING_KEY_PER_USER_DAILY,
    quota_day_for,
)

logger = logging.getLogger(__name__)


class PrayerVoiceQuotaMixin:
    async def ensure_prayer_voice_quota_schema(self) -> None:
        """Идемпотентно поднимает таблицы/колонки (если миграцию не гоняли)."""
        sql = """
        CREATE TABLE IF NOT EXISTS bot_runtime_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        INSERT INTO bot_runtime_settings (key, value)
        VALUES ('prayer_voice_daily_limit', '5')
        ON CONFLICT (key) DO NOTHING;
        INSERT INTO bot_runtime_settings (key, value)
        VALUES ('prayer_voice_per_user_daily', '2')
        ON CONFLICT (key) DO NOTHING;
        CREATE TABLE IF NOT EXISTS prayer_voice_quota_log (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            quota_day DATE NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_prayer_voice_quota_day
            ON prayer_voice_quota_log (quota_day);
        CREATE TABLE IF NOT EXISTS prayer_voice_period (
            quota_day DATE PRIMARY KEY,
            limit_slots INTEGER NOT NULL,
            computed_slots INTEGER NOT NULL DEFAULT 0,
            min_floor INTEGER NOT NULL DEFAULT 0,
            revenue_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
            revenue_rub DOUBLE PRECISION NOT NULL DEFAULT 0,
            locked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS prayer_voice_unlock_pending BOOLEAN NOT NULL DEFAULT FALSE;
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS prayer_last_text TEXT;
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS prayer_last_text_at TIMESTAMPTZ;
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS prayer_voice_bonus INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE payments
            ADD COLUMN IF NOT EXISTS purpose TEXT;
        ALTER TABLE payments
            ADD COLUMN IF NOT EXISTS counts_for_voice_pool BOOLEAN NOT NULL DEFAULT TRUE;
        """
        try:
            async with self.get_connection() as conn:
                await conn.execute(sql)
        except Exception as e:
            logger.warning("ensure_prayer_voice_quota_schema: %s", e)

    async def get_runtime_setting(self, key: str, default: str = "") -> str:
        try:
            async with self.get_connection() as conn:
                val = await conn.fetchval(
                    "SELECT value FROM bot_runtime_settings WHERE key = $1",
                    key,
                )
            return str(val) if val is not None else default
        except Exception as e:
            logger.error("get_runtime_setting %s: %s", key, e)
            return default

    async def set_runtime_setting(self, key: str, value: str) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO bot_runtime_settings (key, value, updated_at)
                    VALUES ($1, $2, NOW())
                    ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_at = NOW()
                    """,
                    key,
                    str(value),
                )
            return True
        except Exception as e:
            logger.error("set_runtime_setting %s: %s", key, e)
            return False

    async def get_prayer_voice_min_limit(self) -> int:
        raw = await self.get_runtime_setting(
            SETTING_KEY_MIN_LIMIT, str(DEFAULT_MIN_LIMIT)
        )
        try:
            return max(0, int(str(raw).strip()))
        except (TypeError, ValueError):
            return DEFAULT_MIN_LIMIT

    async def get_prayer_voice_daily_limit(self) -> int:
        """Совместимость: раньше фиксированный лимит, теперь — минимум (пол)."""
        return await self.get_prayer_voice_min_limit()

    async def set_prayer_voice_daily_limit(self, limit: int) -> bool:
        return await self.set_prayer_voice_min_limit(limit)

    async def set_prayer_voice_min_limit(self, limit: int) -> bool:
        return await self.set_runtime_setting(
            SETTING_KEY_MIN_LIMIT, str(max(0, int(limit)))
        )

    async def get_prayer_voice_per_user_daily(self) -> int:
        raw = await self.get_runtime_setting(
            SETTING_KEY_PER_USER_DAILY, str(DEFAULT_PER_USER_DAILY)
        )
        try:
            return max(0, int(str(raw).strip()))
        except (TypeError, ValueError):
            return DEFAULT_PER_USER_DAILY

    async def set_prayer_voice_per_user_daily(self, limit: int) -> bool:
        return await self.set_runtime_setting(
            SETTING_KEY_PER_USER_DAILY, str(max(0, int(limit)))
        )

    async def count_user_prayer_voice_quota_used(
        self, user_id: int, *, quota_day: Optional[date] = None
    ) -> int:
        day = quota_day or quota_day_for()
        try:
            async with self.get_connection() as conn:
                n = await conn.fetchval(
                    """
                    SELECT COUNT(*)::int
                    FROM prayer_voice_quota_log
                    WHERE quota_day = $1 AND user_id = $2
                    """,
                    day,
                    int(user_id),
                )
            return int(n or 0)
        except Exception as e:
            logger.error("count_user_prayer_voice_quota_used uid=%s: %s", user_id, e)
            return 0

    async def sum_succeeded_payments_rub(
        self, start: datetime, end: datetime
    ) -> float:
        """Сумма всех успешных поступлений в [start, end) в рублях."""
        try:
            async with self.get_connection() as conn:
                val = await conn.fetchval(
                    """
                    SELECT COALESCE(SUM(
                        CASE
                          WHEN amount_rub IS NOT NULL THEN amount_rub::float8
                          WHEN upper(COALESCE(currency, 'RUB')) = 'RUB'
                            THEN amount::float8
                          ELSE 0::float8
                        END
                    ), 0)::float8
                    FROM payments
                    WHERE status = 'succeeded'
                      AND COALESCE(counts_for_voice_pool, TRUE) = TRUE
                      AND completed_at >= $1
                      AND completed_at < $2
                    """,
                    start,
                    end,
                )
            return float(val or 0.0)
        except Exception as e:
            logger.error("sum_succeeded_payments_rub: %s", e)
            return 0.0

    async def get_prayer_voice_period(
        self, quota_day: date
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT quota_day, limit_slots, computed_slots, min_floor,
                           revenue_usd, revenue_rub, locked_at
                    FROM prayer_voice_period
                    WHERE quota_day = $1
                    """,
                    quota_day,
                )
            return dict(row) if row else None
        except Exception as e:
            logger.error("get_prayer_voice_period %s: %s", quota_day, e)
            return None

    async def insert_prayer_voice_period(
        self,
        *,
        quota_day: date,
        limit_slots: int,
        computed_slots: int,
        min_floor: int,
        revenue_usd: float,
        revenue_rub: float,
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO prayer_voice_period (
                        quota_day, limit_slots, computed_slots, min_floor,
                        revenue_usd, revenue_rub, locked_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, NOW())
                    ON CONFLICT (quota_day) DO NOTHING
                    RETURNING quota_day, limit_slots, computed_slots, min_floor,
                              revenue_usd, revenue_rub, locked_at
                    """,
                    quota_day,
                    int(limit_slots),
                    int(computed_slots),
                    int(min_floor),
                    float(revenue_usd),
                    float(revenue_rub),
                )
            if row:
                return dict(row)
            return await self.get_prayer_voice_period(quota_day)
        except Exception as e:
            logger.error("insert_prayer_voice_period %s: %s", quota_day, e)
            return None

    async def update_prayer_voice_period_totals(
        self,
        *,
        quota_day: date,
        limit_slots: int,
        computed_slots: int,
        min_floor: int,
        revenue_usd: float,
        revenue_rub: float,
    ) -> Optional[Dict[str, Any]]:
        """Пересчёт уже зафиксированного периода (ручной донат в текущий пул)."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE prayer_voice_period
                       SET limit_slots = $2,
                           computed_slots = $3,
                           min_floor = $4,
                           revenue_usd = $5,
                           revenue_rub = $6
                     WHERE quota_day = $1
                 RETURNING quota_day, limit_slots, computed_slots, min_floor,
                           revenue_usd, revenue_rub, locked_at
                    """,
                    quota_day,
                    int(limit_slots),
                    int(computed_slots),
                    int(min_floor),
                    float(revenue_usd),
                    float(revenue_rub),
                )
            return dict(row) if row else None
        except Exception as e:
            logger.error("update_prayer_voice_period_totals %s: %s", quota_day, e)
            return None

    async def count_prayer_voice_quota_used(
        self, *, quota_day: Optional[date] = None
    ) -> int:
        day = quota_day or quota_day_for()
        try:
            async with self.get_connection() as conn:
                n = await conn.fetchval(
                    """
                    SELECT COUNT(*)::int
                    FROM prayer_voice_quota_log
                    WHERE quota_day = $1
                    """,
                    day,
                )
            return int(n or 0)
        except Exception as e:
            logger.error("count_prayer_voice_quota_used: %s", e)
            return 0

    async def try_reserve_prayer_voice_slot(
        self,
        user_id: int,
        *,
        quota_day: Optional[date] = None,
        limit_slots: Optional[int] = None,
    ) -> Optional[int]:
        """Атомарно занимает слот. None — лимит исчерпан; иначе id лога."""
        day = quota_day or quota_day_for()
        try:
            async with self.get_connection() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtext('prayer_voice_quota'))"
                    )
                    if limit_slots is None:
                        limit = await conn.fetchval(
                            """
                            SELECT limit_slots
                            FROM prayer_voice_period
                            WHERE quota_day = $1
                            """,
                            day,
                        )
                        if limit is None:
                            limit = await conn.fetchval(
                                """
                                SELECT COALESCE(
                                    (
                                        SELECT NULLIF(trim(value), '')::int
                                        FROM bot_runtime_settings
                                        WHERE key = $1
                                    ),
                                    $2
                                )
                                """,
                                SETTING_KEY_MIN_LIMIT,
                                DEFAULT_MIN_LIMIT,
                            )
                        limit_i = max(0, int(limit or 0))
                    else:
                        limit_i = max(0, int(limit_slots))
                    used = await conn.fetchval(
                        """
                        SELECT COUNT(*)::int
                        FROM prayer_voice_quota_log
                        WHERE quota_day = $1
                        """,
                        day,
                    )
                    if int(used or 0) >= limit_i:
                        return None
                    row_id = await conn.fetchval(
                        """
                        INSERT INTO prayer_voice_quota_log (user_id, quota_day)
                        VALUES ($1, $2)
                        RETURNING id
                        """,
                        int(user_id),
                        day,
                    )
                    return int(row_id) if row_id is not None else None
        except Exception as e:
            logger.error("try_reserve_prayer_voice_slot uid=%s: %s", user_id, e)
            return None

    async def try_acquire_prayer_voice_access(
        self,
        user_id: int,
        *,
        quota_day: Optional[date] = None,
        limit_slots: Optional[int] = None,
        per_user_daily: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        day = quota_day or quota_day_for()
        personal_cap = (
            per_user_daily
            if per_user_daily is not None
            else await self.get_prayer_voice_per_user_daily()
        )
        personal_cap = max(0, int(personal_cap))
        personal_used = await self.count_user_prayer_voice_quota_used(
            user_id, quota_day=day
        )
        # Личный лимит: сверх — только компенсационный бонус (или донат).
        if personal_used >= personal_cap:
            if await self.try_consume_prayer_voice_bonus(user_id):
                return {"source": "bonus", "reason": "per_user_cap"}
            return None

        slot_id = await self.try_reserve_prayer_voice_slot(
            user_id, quota_day=day, limit_slots=limit_slots
        )
        if slot_id is not None:
            return {"source": "quota", "slot_id": int(slot_id)}
        if await self.try_consume_prayer_voice_bonus(user_id):
            return {"source": "bonus", "reason": "pool_exhausted"}
        return None

    async def try_consume_prayer_voice_bonus(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE users
                       SET prayer_voice_bonus = prayer_voice_bonus - 1
                     WHERE user_id = $1
                       AND COALESCE(prayer_voice_bonus, 0) > 0
                 RETURNING prayer_voice_bonus
                    """,
                    int(user_id),
                )
            return row is not None
        except Exception as e:
            logger.error("try_consume_prayer_voice_bonus uid=%s: %s", user_id, e)
            return False

    async def grant_prayer_voice_bonus(
        self, user_id: int, amount: int = 1
    ) -> bool:
        n = max(1, int(amount))
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE users
                       SET prayer_voice_bonus = COALESCE(prayer_voice_bonus, 0) + $2
                     WHERE user_id = $1
                    """,
                    int(user_id),
                    n,
                )
            return True
        except Exception as e:
            logger.error("grant_prayer_voice_bonus uid=%s: %s", user_id, e)
            return False

    async def release_prayer_voice_access(
        self, access: Optional[Dict[str, Any]], user_id: int
    ) -> None:
        if not access:
            return
        source = access.get("source")
        if source == "quota":
            await self.release_prayer_voice_slot(access.get("slot_id"))
        elif source == "bonus":
            await self.grant_prayer_voice_bonus(user_id, 1)

    async def release_prayer_voice_slot(self, slot_id: Optional[int]) -> None:
        if not slot_id:
            return
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "DELETE FROM prayer_voice_quota_log WHERE id = $1",
                    int(slot_id),
                )
        except Exception as e:
            logger.warning("release_prayer_voice_slot %s: %s", slot_id, e)

    async def save_prayer_last_text(self, user_id: int, text: str) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE users
                       SET prayer_last_text = $2,
                           prayer_last_text_at = NOW()
                     WHERE user_id = $1
                    """,
                    int(user_id),
                    (text or "").strip() or None,
                )
            return True
        except Exception as e:
            logger.error("save_prayer_last_text uid=%s: %s", user_id, e)
            return False

    async def set_prayer_voice_unlock_pending(
        self, user_id: int, pending: bool = True
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE users
                       SET prayer_voice_unlock_pending = $2
                     WHERE user_id = $1
                    """,
                    int(user_id),
                    bool(pending),
                )
            return True
        except Exception as e:
            logger.error("set_prayer_voice_unlock_pending uid=%s: %s", user_id, e)
            return False

    async def get_prayer_voice_unlock_pending(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                val = await conn.fetchval(
                    """
                    SELECT COALESCE(prayer_voice_unlock_pending, FALSE)
                    FROM users WHERE user_id = $1
                    """,
                    int(user_id),
                )
            return bool(val)
        except Exception as e:
            logger.error("get_prayer_voice_unlock_pending uid=%s: %s", user_id, e)
            return False

    async def get_prayer_last_text(self, user_id: int) -> Optional[str]:
        try:
            async with self.get_connection() as conn:
                val = await conn.fetchval(
                    "SELECT prayer_last_text FROM users WHERE user_id = $1",
                    int(user_id),
                )
            text = (val or "").strip()
            return text or None
        except Exception as e:
            logger.error("get_prayer_last_text uid=%s: %s", user_id, e)
            return None

    async def take_prayer_voice_unlock(
        self, user_id: int, *, require_pending: bool = True
    ) -> Optional[str]:
        try:
            async with self.get_connection() as conn:
                async with conn.transaction():
                    row = await conn.fetchrow(
                        """
                        SELECT prayer_voice_unlock_pending, prayer_last_text
                        FROM users
                        WHERE user_id = $1
                        FOR UPDATE
                        """,
                        int(user_id),
                    )
                    if not row:
                        return None
                    if require_pending and not row["prayer_voice_unlock_pending"]:
                        return None
                    text = (row["prayer_last_text"] or "").strip()
                    await conn.execute(
                        """
                        UPDATE users
                           SET prayer_voice_unlock_pending = FALSE
                         WHERE user_id = $1
                        """,
                        int(user_id),
                    )
                    return text or None
        except Exception as e:
            logger.error("take_prayer_voice_unlock uid=%s: %s", user_id, e)
            return None

    async def get_prayer_quota_status(self) -> Dict[str, Any]:
        day = quota_day_for()
        period = await self.get_prayer_voice_period(day)
        min_floor = await self.get_prayer_voice_min_limit()
        limit = int(period["limit_slots"]) if period else min_floor
        used = await self.count_prayer_voice_quota_used(quota_day=day)
        return {
            "quota_day": day,
            "limit": limit,
            "used": used,
            "remaining": max(0, limit - used),
            "min_floor": min_floor,
            "computed_slots": int((period or {}).get("computed_slots") or 0),
            "next_slots": 0,
            "per_user_daily": await self.get_prayer_voice_per_user_daily(),
        }
