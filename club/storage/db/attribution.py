"""Маркетинговые касания (attribution_touches)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from bot.services.attribution_touch import ParsedTouch

logger = logging.getLogger(__name__)


class AttributionMixin:
    async def record_attribution_touch(
        self,
        user_id: int,
        parsed: "ParsedTouch",
        *,
        source_type: str,
        created_at: Optional[datetime] = None,
    ) -> bool:
        """Пишет касание и при необходимости обновляет first_touch на users."""
        try:
            ref_key = parsed.ref_key
            channel_type = None
            if ref_key:
                channel_type = await self._attribution_channel_for_ref(ref_key)

            ts = created_at
            async with self.get_connection() as conn:
                touch_at = ts
                if touch_at is None:
                    touch_at = await conn.fetchval("SELECT NOW()")
                await conn.execute(
                    """
                    INSERT INTO attribution_touches (
                        user_id, touch_key, touch_kind, source_type,
                        ref_key, channel_type, raw_payload, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (user_id, touch_key, source_type, created_at)
                    DO NOTHING
                    """,
                    user_id,
                    parsed.touch_key,
                    parsed.touch_kind,
                    source_type,
                    ref_key,
                    channel_type,
                    parsed.raw_payload,
                    touch_at,
                )

                await conn.execute(
                    """
                    UPDATE users SET
                        first_touch_key = COALESCE(first_touch_key, $2),
                        first_touch_kind = COALESCE(first_touch_kind, $3),
                        first_touch_at = COALESCE(first_touch_at, COALESCE($4, NOW()))
                    WHERE user_id = $1
                      AND first_touch_key IS NULL
                    """,
                    user_id,
                    parsed.touch_key,
                    parsed.touch_kind,
                    ts,
                )
            return True
        except Exception as e:
            logger.error("record_attribution_touch user=%s: %s", user_id, e)
            return False

    async def _attribution_channel_for_ref(self, ref_key: str) -> Optional[str]:
        try:
            async with self.get_connection() as conn:
                return await conn.fetchval(
                    "SELECT type FROM ref_keys WHERE ref_key = $1",
                    ref_key,
                )
        except Exception:
            return None

    _MEANINGFUL_TOUCH_SQL = """
        touch_key NOT LIKE 'payment_select_%'
        AND touch_key NOT LIKE 'payment_currency_rub_%'
        AND touch_key NOT LIKE 'payment_currency_usd_%'
    """

    async def get_last_marketing_touch_before(
        self,
        user_id: int,
        before_at: datetime,
        *,
        meaningful_only: bool = False,
    ) -> Optional[Dict[str, Any]]:
        try:
            meaningful_clause = f"AND {self._MEANINGFUL_TOUCH_SQL}" if meaningful_only else ""
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    f"""
                    SELECT touch_key, touch_kind, ref_key, channel_type, created_at
                    FROM attribution_touches
                    WHERE user_id = $1 AND created_at <= $2
                      {meaningful_clause}
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    user_id,
                    before_at,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_last_marketing_touch user=%s: %s", user_id, e)
            return None

    async def get_first_meaningful_marketing_touch(
        self, user_id: int
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    f"""
                    SELECT touch_key, touch_kind, ref_key, channel_type, created_at
                    FROM attribution_touches
                    WHERE user_id = $1
                      AND {self._MEANINGFUL_TOUCH_SQL}
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    user_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_first_meaningful_touch user=%s: %s", user_id, e)
            return None

    async def set_order_pay_attribution(
        self,
        order_id: int,
        user_id: int,
        paid_at: datetime,
    ) -> None:
        touch = await self.get_last_marketing_touch_before(
            user_id, paid_at, meaningful_only=True
        )
        if not touch:
            return
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE orders SET
                        pay_last_touch_key = $2,
                        pay_last_touch_at = $3
                    WHERE id = $1
                    """,
                    order_id,
                    touch["touch_key"],
                    touch["created_at"],
                )
        except Exception as e:
            logger.error("set_order_pay_attribution order=%s: %s", order_id, e)
