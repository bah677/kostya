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

    async def get_marathon_stats(self, marathon_id: int) -> Dict[str, Any]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        COUNT(*)::int AS contributions_count,
                        COUNT(DISTINCT user_id)::int AS donors_count,
                        COALESCE(SUM(amount_goal), 0)::numeric AS raised_amount,
                        COALESCE(AVG(amount_goal), 0)::numeric AS avg_amount,
                        COALESCE(MAX(amount_goal), 0)::numeric AS max_amount,
                        COALESCE(MIN(amount_goal), 0)::numeric AS min_amount,
                        MIN(created_at) AS first_contribution_at,
                        MAX(created_at) AS last_contribution_at
                    FROM donation_marathon_contributions
                    WHERE marathon_id = $1
                    """,
                    marathon_id,
                )
                return dict(row) if row else {}
        except Exception as e:
            logger.error("❌ get_marathon_stats: %s", e)
            return {}

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

    async def get_marathon_contribution_by_payment_id(
        self, payment_id: int
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM donation_marathon_contributions
                     WHERE payment_id = $1
                    """,
                    payment_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("❌ get_marathon_contribution_by_payment_id: %s", e)
            return None

    async def list_recent_marathons(self, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT *
                    FROM donation_marathons
                    ORDER BY COALESCE(closed_at, started_at, created_at) DESC, id DESC
                    LIMIT $1
                    """,
                    limit,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("❌ list_recent_marathons: %s", e)
            return []

    async def list_marathon_participant_user_ids(
        self, marathon_ids: List[int]
    ) -> List[int]:
        ids = sorted({int(x) for x in marathon_ids if int(x) > 0})
        if not ids:
            return []
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT c.user_id
                    FROM donation_marathon_contributions c
                    JOIN users u ON u.user_id = c.user_id
                    WHERE c.marathon_id = ANY($1::bigint[])
                      AND u.is_active = TRUE
                    ORDER BY c.user_id ASC
                    """,
                    ids,
                )
                return [int(r["user_id"]) for r in rows]
        except Exception as e:
            logger.error("❌ list_marathon_participant_user_ids: %s", e)
            return []

    async def list_standalone_payments_for_marathon_backfill(
        self, marathon: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Успешные донаты (order_id IS NULL) в периоде марафона без записи в contributions."""
        started = marathon.get("started_at") or marathon.get("created_at")
        ended = marathon.get("closed_at")
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT p.*
                      FROM payments p
                     WHERE p.status = 'succeeded'
                       AND p.order_id IS NULL
                       AND COALESCE(p.completed_at, p.updated_at, p.created_at) >= $1
                       AND ($2::timestamptz IS NULL
                            OR COALESCE(p.completed_at, p.updated_at, p.created_at) <= $2)
                       AND NOT EXISTS (
                             SELECT 1 FROM donation_marathon_contributions c
                              WHERE c.payment_id = p.id
                           )
                     ORDER BY COALESCE(p.completed_at, p.updated_at, p.created_at) ASC
                    """,
                    started,
                    ended,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("❌ list_standalone_payments_for_marathon_backfill: %s", e)
            return []

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
