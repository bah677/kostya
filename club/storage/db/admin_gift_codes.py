"""Mixin: одноразовые админские gift-ссылки (`admin_gift_codes`)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class AdminGiftCodesMixin:
    async def create_admin_gift_code(
        self,
        *,
        gift_code: str,
        days: int,
        created_by: int,
        expires_at: datetime,
        note: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO admin_gift_codes
                        (gift_code, days, created_by, note, expires_at)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING *
                    """,
                    gift_code,
                    days,
                    created_by,
                    (note or "").strip() or None,
                    expires_at,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("create_admin_gift_code: %s", e, exc_info=True)
            return None

    async def get_admin_gift_code(self, gift_code: str) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM admin_gift_codes WHERE gift_code = $1",
                    gift_code,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_admin_gift_code: %s", e)
            return None

    async def claim_admin_gift_code(
        self,
        gift_code: str,
        *,
        activated_by: int,
        now: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Атомарно помечает код used. None — уже использован / истёк / нет."""
        ts = now or datetime.now()
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE admin_gift_codes
                    SET status = 'used',
                        activated_by = $2,
                        activated_at = $3
                    WHERE gift_code = $1
                      AND status = 'active'
                      AND expires_at > $3
                    RETURNING *
                    """,
                    gift_code,
                    activated_by,
                    ts,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(
                "claim_admin_gift_code code=%s uid=%s: %s",
                gift_code,
                activated_by,
                e,
                exc_info=True,
            )
            return None

    async def release_admin_gift_code_claim(self, gift_code: str) -> None:
        """Откат claim при сбое выдачи лицензии."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE admin_gift_codes
                    SET status = 'active',
                        activated_by = NULL,
                        activated_at = NULL
                    WHERE gift_code = $1 AND status = 'used'
                    """,
                    gift_code,
                )
        except Exception as e:
            logger.error("release_admin_gift_code_claim code=%s: %s", gift_code, e)
