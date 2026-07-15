"""
Mixin: ангельские взносы (случайные продления после оплаты).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Типы лицензий, участники с которыми могут получить ангельское продление.
ANGEL_POOL_ELIGIBLE_LICENSE_TYPES = (
    "subscription",
    "admin_grant",
    "bonus",
    "bonus_extension",
)


class AngelPoolMixin:

    async def angel_pool_recipients_exist_for_order(self, order_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                val = await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM angel_pool_recipients WHERE order_id = $1
                    )
                    """,
                    order_id,
                )
                return bool(val)
        except Exception as e:
            logger.error("angel_pool_recipients_exist order=%s: %s", order_id, e)
            return False

    async def get_angel_pool_eligible_members(
        self,
        *,
        exclude_user_id: int,
        days_left_max: int = 3,
    ) -> List[Dict[str, Any]]:
        """Активная лицензия, до окончания ≤ days_left_max дней (включая бонусный день)."""
        try:
            types = list(ANGEL_POOL_ELIGIBLE_LICENSE_TYPES)
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT l.user_id, l.expires_at, l.license_type, u.first_name
                    FROM license l
                    JOIN users u ON u.user_id = l.user_id
                    WHERE l.status = 'active'
                      AND l.expires_at > NOW()
                      AND l.expires_at <= NOW() + ($2::int * INTERVAL '1 day')
                      AND u.is_active = TRUE
                      AND l.license_type = ANY($3::text[])
                      AND l.user_id <> $1
                    ORDER BY l.expires_at ASC
                    """,
                    exclude_user_id,
                    days_left_max,
                    types,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error(
                "get_angel_pool_eligible exclude=%s: %s", exclude_user_id, e
            )
            return []

    async def count_prior_angel_pool_wins(self, user_ids: List[int]) -> Dict[int, int]:
        if not user_ids:
            return {}
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT recipient_user_id, COUNT(*) AS cnt
                    FROM angel_pool_recipients
                    WHERE recipient_user_id = ANY($1::bigint[])
                    GROUP BY recipient_user_id
                    """,
                    user_ids,
                )
                return {int(r["recipient_user_id"]): int(r["cnt"]) for r in rows}
        except Exception as e:
            logger.error("count_prior_angel_pool_wins: %s", e)
            return {}

    async def record_angel_pool_recipients(
        self,
        *,
        order_id: int,
        payment_id: int,
        donor_user_id: int,
        recipient_user_ids: List[int],
    ) -> bool:
        if not recipient_user_ids:
            return True
        try:
            async with self.get_connection() as conn:
                await conn.executemany(
                    """
                    INSERT INTO angel_pool_recipients
                        (order_id, payment_id, donor_user_id, recipient_user_id)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (order_id, recipient_user_id) DO NOTHING
                    """,
                    [
                        (order_id, payment_id, donor_user_id, rid)
                        for rid in recipient_user_ids
                    ],
                )
            return True
        except Exception as e:
            logger.error(
                "record_angel_pool_recipients order=%s: %s", order_id, e
            )
            return False
