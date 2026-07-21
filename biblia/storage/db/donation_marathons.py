"""
Mixin: марафоны сбора пожертвований.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class DonationMarathonsMixin:
    async def get_active_donation_marathon(self) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT *
                      FROM donation_marathons
                     WHERE status = 'active'
                     ORDER BY id DESC
                     LIMIT 1
                    """
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("❌ get_active_donation_marathon: %s", e)
            return None

    async def get_donation_marathon(self, marathon_id: int) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM donation_marathons WHERE id = $1",
                    marathon_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("❌ get_donation_marathon: %s", e)
            return None

    async def create_donation_marathon(
        self,
        *,
        name: str,
        description_html: str,
        goal_amount: float,
        goal_currency: str,
        accept_rub: bool,
        accept_usd: bool,
        accept_crypto: bool,
        created_by: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO donation_marathons (
                      name, description_html, goal_amount, goal_currency,
                      accept_rub, accept_usd, accept_crypto, created_by
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    RETURNING *
                    """,
                    name.strip(),
                    description_html.strip(),
                    goal_amount,
                    goal_currency.upper(),
                    accept_rub,
                    accept_usd,
                    accept_crypto,
                    created_by,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("❌ create_donation_marathon: %s", e)
            return None

    async def close_donation_marathon(
        self,
        marathon_id: int,
        *,
        close_reason: str,
        status: str = "completed",
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                tag = await conn.execute(
                    """
                    UPDATE donation_marathons
                       SET status = $2,
                           closed_at = NOW(),
                           close_reason = $3
                     WHERE id = $1
                       AND status = 'active'
                    """,
                    marathon_id,
                    status,
                    close_reason,
                )
                return tag.endswith("1")
        except Exception as e:
            logger.error("❌ close_donation_marathon: %s", e)
            return False

    async def get_marathon_raised_amount(self, marathon_id: int) -> float:
        try:
            async with self.get_connection() as conn:
                val = await conn.fetchval(
                    """
                    SELECT COALESCE(SUM(amount_goal), 0)
                      FROM donation_marathon_contributions
                     WHERE marathon_id = $1
                    """,
                    marathon_id,
                )
                return float(val or 0)
        except Exception as e:
            logger.error("❌ get_marathon_raised_amount: %s", e)
            return 0.0

    async def get_marathon_donors_count(self, marathon_id: int) -> int:
        try:
            async with self.get_connection() as conn:
                val = await conn.fetchval(
                    """
                    SELECT COUNT(DISTINCT user_id)
                      FROM donation_marathon_contributions
                     WHERE marathon_id = $1
                    """,
                    marathon_id,
                )
                return int(val or 0)
        except Exception as e:
            logger.error("❌ get_marathon_donors_count: %s", e)
            return 0

    async def add_marathon_contribution(
        self,
        *,
        marathon_id: int,
        user_id: int,
        amount_goal: float,
        amount_original: Optional[float] = None,
        currency_original: Optional[str] = None,
        payment_id: Optional[int] = None,
        source: str = "payment",
        note: Optional[str] = None,
        created_by: Optional[int] = None,
        goal_currency: Optional[str] = None,
        amount_rub: Optional[float] = None,
        rub_per_goal_unit: Optional[float] = None,
        rate_original_to_goal: Optional[float] = None,
        fx_source: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO donation_marathon_contributions (
                      marathon_id, user_id, amount_goal, amount_original,
                      currency_original, payment_id, source, note, created_by,
                      goal_currency, amount_rub, rub_per_goal_unit,
                      rate_original_to_goal, fx_source
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                    ON CONFLICT (payment_id) WHERE payment_id IS NOT NULL DO NOTHING
                    RETURNING *
                    """,
                    marathon_id,
                    user_id,
                    amount_goal,
                    amount_original,
                    (currency_original or "").upper() or None,
                    payment_id,
                    source,
                    note,
                    created_by,
                    (goal_currency or "").upper() or None,
                    amount_rub,
                    rub_per_goal_unit,
                    rate_original_to_goal,
                    fx_source,
                )
                if row:
                    return dict(row)
                if payment_id is not None:
                    existing = await conn.fetchrow(
                        """
                        SELECT * FROM donation_marathon_contributions
                         WHERE payment_id = $1
                        """,
                        payment_id,
                    )
                    return dict(existing) if existing else None
                return None
        except Exception as e:
            logger.error("❌ add_marathon_contribution: %s", e)
            return None

    async def list_user_ids_with_min_user_questions(self, min_questions: int) -> List[int]:
        """Активные пользователи с ≥ N сообщений role=user (вопросы боту)."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT m.user_id
                      FROM messages m
                      JOIN users u ON u.user_id = m.user_id AND u.is_active = TRUE
                     WHERE m.role = 'user'
                     GROUP BY m.user_id
                    HAVING COUNT(*) >= $1
                     ORDER BY m.user_id ASC
                    """,
                    min_questions,
                )
                return [int(r["user_id"]) for r in rows]
        except Exception as e:
            logger.error("❌ list_user_ids_with_min_user_questions: %s", e)
            return []
