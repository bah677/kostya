"""
Mixin: рекуррентные донаты (`donation_subscriptions`).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


class DonationSubscriptionsMixin:

    async def create_donation_subscription(
        self,
        *,
        user_id: int,
        bzb_subscription_id: str,
        bzb_payment_link_id: str,
        amount: float,
        currency: str,
        status: str = "PENDING",
        interval_unit: str = "MONTH",
        interval_count: int = 1,
        last_charge_at: Optional[datetime] = None,
        next_charge_at: Optional[datetime] = None,
        started_at: Optional[datetime] = None,
        initial_payment_id: Optional[int] = None,
    ) -> Optional[int]:
        try:
            async with self.get_connection() as conn:
                row_id = await conn.fetchval(
                    """
                    INSERT INTO donation_subscriptions (
                        user_id, bzb_subscription_id, bzb_payment_link_id,
                        amount, currency, status, interval_unit, interval_count,
                        last_charge_at, next_charge_at, started_at,
                        initial_payment_id, updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, NOW())
                    ON CONFLICT (bzb_subscription_id) DO UPDATE
                       SET status = EXCLUDED.status,
                           last_charge_at = EXCLUDED.last_charge_at,
                           next_charge_at = EXCLUDED.next_charge_at,
                           updated_at = NOW()
                    RETURNING id
                    """,
                    user_id,
                    bzb_subscription_id,
                    bzb_payment_link_id,
                    amount,
                    currency.upper(),
                    status,
                    interval_unit,
                    interval_count,
                    last_charge_at,
                    next_charge_at,
                    started_at,
                    initial_payment_id,
                )
                return int(row_id) if row_id else None
        except Exception as e:
            logger.error("❌ create_donation_subscription: %s", e, exc_info=True)
            return None

    async def get_donation_subscription(self, sub_id: int) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM donation_subscriptions WHERE id = $1",
                    sub_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("❌ get_donation_subscription id=%s: %s", sub_id, e)
            return None

    async def get_user_active_donation_subscription(
        self, user_id: int
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM donation_subscriptions
                     WHERE user_id = $1
                       AND status IN ('PENDING', 'ACTIVE', 'PAST_DUE')
                     ORDER BY created_at DESC
                     LIMIT 1
                    """,
                    user_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("❌ get_user_active_donation_subscription uid=%s: %s", user_id, e)
            return None

    async def list_pollable_donation_subscriptions(self) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM donation_subscriptions
                     WHERE status IN ('PENDING', 'ACTIVE', 'PAST_DUE')
                     ORDER BY updated_at NULLS FIRST, created_at ASC
                    """
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("❌ list_pollable_donation_subscriptions: %s", e)
            return []

    async def update_donation_subscription_from_bzb(
        self,
        sub_id: int,
        *,
        status: str,
        last_charge_at: Optional[datetime] = None,
        next_charge_at: Optional[datetime] = None,
        canceled_at: Optional[datetime] = None,
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE donation_subscriptions
                       SET status = $2,
                           last_charge_at = COALESCE($3, last_charge_at),
                           next_charge_at = $4,
                           canceled_at = COALESCE($5, canceled_at),
                           updated_at = NOW()
                     WHERE id = $1
                    """,
                    sub_id,
                    status,
                    last_charge_at,
                    next_charge_at,
                    canceled_at,
                )
                return True
        except Exception as e:
            logger.error("❌ update_donation_subscription_from_bzb id=%s: %s", sub_id, e)
            return False

    async def mark_donation_subscription_canceled(self, sub_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE donation_subscriptions
                       SET status = 'CANCELED',
                           canceled_at = NOW(),
                           updated_at = NOW()
                     WHERE id = $1
                    """,
                    sub_id,
                )
                return True
        except Exception as e:
            logger.error("❌ mark_donation_subscription_canceled id=%s: %s", sub_id, e)
            return False

    @staticmethod
    def bzb_subscription_fields(sub: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": (sub.get("status") or "PENDING").upper(),
            "last_charge_at": _parse_dt(sub.get("last_charge_at")),
            "next_charge_at": _parse_dt(sub.get("next_charge_at")),
            "started_at": _parse_dt(sub.get("started_at")),
            "canceled_at": _parse_dt(sub.get("canceled_at")),
        }
