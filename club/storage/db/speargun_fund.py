"""Отдельная таблица speargun.donations — не выручка клуба."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SpeargunFundMixin:
    async def speargun_create_donation(
        self,
        *,
        source: str,
        amount: Decimal,
        currency: str,
        amount_rub: Decimal,
        telegram_user_id: Optional[int] = None,
        telegram_username: Optional[str] = None,
        donor_name: Optional[str] = None,
        yookassa_payment_id: Optional[str] = None,
        confirmation_url: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> Optional[int]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO speargun.donations (
                        source, telegram_user_id, telegram_username, donor_name,
                        amount, currency, amount_rub,
                        yookassa_payment_id, confirmation_url, status, meta
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, 'pending', $10::jsonb
                    )
                    RETURNING id
                    """,
                    source,
                    telegram_user_id,
                    telegram_username,
                    donor_name,
                    amount,
                    currency.upper(),
                    amount_rub,
                    yookassa_payment_id,
                    confirmation_url,
                    json.dumps(meta or {}, ensure_ascii=False),
                )
                return int(row["id"]) if row else None
        except Exception as e:
            logger.error("speargun_create_donation: %s", e)
            return None

    async def speargun_attach_yookassa(
        self,
        donation_id: int,
        *,
        yookassa_payment_id: str,
        confirmation_url: str,
    ) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE speargun.donations
                    SET yookassa_payment_id = $2,
                        confirmation_url = $3,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    donation_id,
                    yookassa_payment_id,
                    confirmation_url,
                )
        except Exception as e:
            logger.error("speargun_attach_yookassa id=%s: %s", donation_id, e)

    async def speargun_list_pending(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, yookassa_payment_id, amount, currency, amount_rub,
                           telegram_user_id
                    FROM speargun.donations
                    WHERE status = 'pending'
                      AND yookassa_payment_id IS NOT NULL
                    ORDER BY id
                    LIMIT $1
                    """,
                    limit,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("speargun_list_pending: %s", e)
            return []

    async def speargun_mark_status(
        self, donation_id: int, status: str, *, paid: bool = False
    ) -> None:
        try:
            async with self.get_connection() as conn:
                paid_at = datetime.now(timezone.utc) if paid else None
                await conn.execute(
                    """
                    UPDATE speargun.donations
                    SET status = $2,
                        paid_at = COALESCE($3, paid_at),
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    donation_id,
                    status,
                    paid_at,
                )
        except Exception as e:
            logger.error("speargun_mark_status id=%s: %s", donation_id, e)

    async def speargun_totals(self) -> Dict[str, Any]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        COALESCE(SUM(amount_rub) FILTER (WHERE status = 'succeeded'), 0)
                            AS raised_rub,
                        COUNT(*) FILTER (WHERE status = 'succeeded') AS donors
                    FROM speargun.donations
                    """
                )
                return {
                    "raised_rub": float(row["raised_rub"] or 0),
                    "donors": int(row["donors"] or 0),
                }
        except Exception as e:
            logger.error("speargun_totals: %s", e)
            return {"raised_rub": 0.0, "donors": 0}
