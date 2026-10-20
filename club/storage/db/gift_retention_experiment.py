"""Mixin: тестовые когорты удержания подарочной волны (gift_retention_experiment).

Раскладку делает `scripts/assign_gift_retention_cohorts.py` один раз, дальше
вариант пользователя не меняется: `assign_gift_retention_variant` вставляет
только новую строку и не трогает существующую. Иначе человек в середине замера
переедет в другую когорту и сравнивать будет нечего.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CAMPAIGN_GIFT_2026_09 = "gift-2026-09"

VARIANT_CONTROL = "control"
VARIANT_P1_DISCOUNT = "p1_discount"
VARIANT_P2_SELL_5D = "p2_sell5d"
VARIANT_P3_GROUP_CLOSE = "p3_group_close"
VARIANT_P4_STEP_299 = "p4_step299"

#: Варианты, которые что-то меняют в письмах (контроль идёт как было).
ACTIVE_VARIANTS = (
    VARIANT_P1_DISCOUNT,
    VARIANT_P2_SELL_5D,
    VARIANT_P3_GROUP_CLOSE,
    VARIANT_P4_STEP_299,
)

ALL_VARIANTS = (VARIANT_CONTROL,) + ACTIVE_VARIANTS


class GiftRetentionExperimentMixin:
    async def get_gift_retention_variant(self, user_id: int) -> Optional[str]:
        """Вариант пользователя или None, если он не в эксперименте."""
        try:
            async with self.get_connection() as conn:
                return await conn.fetchval(
                    "SELECT variant FROM gift_retention_experiment WHERE user_id = $1",
                    int(user_id),
                )
        except Exception as e:
            logger.error("get_gift_retention_variant uid=%s: %s", user_id, e)
            return None

    async def assign_gift_retention_variant(
        self,
        user_id: int,
        *,
        campaign: str,
        variant: str,
        stratum: str = "",
        expires_on: Optional[date] = None,
        assign_seed: str = "",
    ) -> bool:
        """Записывает вариант. Уже назначенный не перезаписывает."""
        if variant not in ALL_VARIANTS:
            logger.error("assign_gift_retention_variant: неизвестный вариант %r", variant)
            return False
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO gift_retention_experiment
                        (user_id, campaign, variant, stratum, expires_on, assign_seed)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    int(user_id),
                    campaign,
                    variant,
                    stratum,
                    expires_on,
                    assign_seed,
                )
            return True
        except Exception as e:
            logger.error("assign_gift_retention_variant uid=%s: %s", user_id, e)
            return False

    async def gift_retention_counts(self, campaign: str) -> Dict[str, int]:
        """Сколько человек в каждом варианте."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT variant, count(*) AS n
                    FROM gift_retention_experiment
                    WHERE campaign = $1
                    GROUP BY variant
                    """,
                    campaign,
                )
            return {r["variant"]: int(r["n"]) for r in rows}
        except Exception as e:
            logger.error("gift_retention_counts %s: %s", campaign, e)
            return {}

    async def gift_retention_members(
        self, campaign: str, *, variant: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Состав когорт — для отчёта по результатам."""
        try:
            async with self.get_connection() as conn:
                if variant:
                    rows = await conn.fetch(
                        """
                        SELECT user_id, variant, stratum, expires_on, assigned_at
                        FROM gift_retention_experiment
                        WHERE campaign = $1 AND variant = $2
                        ORDER BY expires_on, user_id
                        """,
                        campaign,
                        variant,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT user_id, variant, stratum, expires_on, assigned_at
                        FROM gift_retention_experiment
                        WHERE campaign = $1
                        ORDER BY variant, expires_on, user_id
                        """,
                        campaign,
                    )
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("gift_retention_members %s: %s", campaign, e)
            return []
