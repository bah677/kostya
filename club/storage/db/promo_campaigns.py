"""
Mixin: промо-кампании (deep link /start=promo_<guid>) и назначения пользователям.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PromoCampaignsMixin:
    async def create_promo_campaign(
        self,
        *,
        name: str,
        description: str,
        discount_percent: float,
        created_by: Optional[int] = None,
        guid: Optional[str] = None,
    ) -> Optional[str]:
        campaign_guid = (guid or uuid.uuid4().hex).strip().lower()
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO promo_campaigns
                        (guid, name, description, discount_percent, created_by)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    campaign_guid,
                    name,
                    description,
                    Decimal(str(discount_percent)),
                    created_by,
                )
            logger.info("Promo campaign created guid=%s name=%r", campaign_guid, name)
            return campaign_guid
        except Exception as e:
            logger.error("Failed to create promo campaign: %s", e)
            return None

    async def get_promo_campaign_by_guid(self, guid: str) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT guid, name, description, discount_percent, is_active, created_at, created_by
                    FROM promo_campaigns
                    WHERE guid = $1
                    """,
                    guid.strip().lower(),
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("Failed to get promo campaign %s: %s", guid, e)
            return None

    async def list_promo_campaigns(self, *, active_only: bool = False) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                q = """
                    SELECT guid, name, description, discount_percent, is_active, created_at, created_by
                    FROM promo_campaigns
                """
                if active_only:
                    q += " WHERE is_active = TRUE"
                q += " ORDER BY created_at DESC"
                rows = await conn.fetch(q)
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("Failed to list promo campaigns: %s", e)
            return []

    async def assign_user_promo_campaign(self, user_id: int, campaign_guid: str) -> bool:
        """Назначает активную кампанию пользователю (перезаписывает предыдущую активную)."""
        guid = campaign_guid.strip().lower()
        campaign = await self.get_promo_campaign_by_guid(guid)
        if not campaign or not campaign.get("is_active"):
            return False
        try:
            async with self.get_connection() as conn:
                updated = await conn.fetchval(
                    """
                    UPDATE user_promo_assignments
                    SET campaign_guid = $2, assigned_at = NOW()
                    WHERE user_id = $1 AND consumed_at IS NULL
                    RETURNING id
                    """,
                    user_id,
                    guid,
                )
                if updated:
                    return True
                await conn.execute(
                    """
                    INSERT INTO user_promo_assignments (user_id, campaign_guid)
                    VALUES ($1, $2)
                    """,
                    user_id,
                    guid,
                )
            logger.info("User %s assigned promo campaign %s", user_id, guid)
            return True
        except Exception as e:
            logger.error(
                "Failed to assign promo %s to user %s: %s", guid, user_id, e
            )
            return False

    async def get_active_user_promo_campaign(
        self, user_id: int
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        a.id AS assignment_id,
                        a.user_id,
                        a.campaign_guid,
                        a.assigned_at,
                        c.name,
                        c.description,
                        c.discount_percent,
                        c.is_active
                    FROM user_promo_assignments a
                    JOIN promo_campaigns c ON c.guid = a.campaign_guid
                    WHERE a.user_id = $1
                      AND a.consumed_at IS NULL
                      AND c.is_active = TRUE
                    """,
                    user_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("Failed to get active promo for user %s: %s", user_id, e)
            return None

    async def consume_user_promo_campaign(
        self, user_id: int, *, payment_id: Optional[int] = None
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                result = await conn.execute(
                    """
                    UPDATE user_promo_assignments
                    SET consumed_at = NOW(),
                        consumed_payment_id = $2
                    WHERE user_id = $1 AND consumed_at IS NULL
                    """,
                    user_id,
                    payment_id,
                )
            if result and result.endswith("1"):
                logger.info(
                    "Promo consumed for user %s payment_id=%s", user_id, payment_id
                )
                return True
            return False
        except Exception as e:
            logger.error("Failed to consume promo for user %s: %s", user_id, e)
            return False
