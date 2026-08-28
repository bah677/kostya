"""
Идемпотентность рассылок `SubscriptionReminderFeature` — таблица `subscription_outreach_sent`.
"""

import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)


class SubscriptionOutreachMixin:
    async def try_claim_subscription_outreach(
        self,
        user_id: int,
        outreach_slug: str,
        sent_on_date: date,
    ) -> bool:
        """
        Зарезервировать слот отправки (строго один раз на связку user + slug + день).

        Returns:
            True — запись создана, можно отправлять; False — дубль или ошибка записи.
        """
        slug = (outreach_slug or "").strip()
        if not slug:
            logger.warning("try_claim_subscription_outreach: empty slug user=%s", user_id)
            return False
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO subscription_outreach_sent (user_id, outreach_slug, sent_on_date)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id, outreach_slug, sent_on_date) DO NOTHING
                    RETURNING id
                    """,
                    user_id,
                    slug,
                    sent_on_date,
                )
                return row is not None
        except Exception as e:
            logger.error(
                "try_claim_subscription_outreach uid=%s slug=%s: %s",
                user_id,
                slug,
                e,
                exc_info=True,
            )
            return False

    async def has_subscription_outreach_since(
        self,
        user_id: int,
        outreach_slug: str,
        since: datetime,
    ) -> bool:
        slug = (outreach_slug or "").strip()
        if not slug:
            return False
        try:
            async with self.get_connection() as conn:
                val = await conn.fetchval(
                    """
                    SELECT 1 FROM subscription_outreach_sent
                     WHERE user_id = $1
                       AND outreach_slug = $2
                       AND created_at >= $3
                     LIMIT 1
                    """,
                    int(user_id),
                    slug,
                    since,
                )
                return val is not None
        except Exception as e:
            logger.error(
                "has_subscription_outreach_since uid=%s slug=%s: %s",
                user_id,
                slug,
                e,
            )
            return False
