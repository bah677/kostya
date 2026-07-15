"""
Mixin: доска желаний (wish_requests, wish_events, user_generosity_stats).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ACTIVE_REQUESTER_STATUSES = (
    "pending_moderation",
    "open",
    "taken",
    "done_pending",
)
POOL_STATUSES = ("open",)
TAKEN_STATUS = "taken"


class WishBoardMixin:
    async def wish_create(
        self,
        *,
        requester_user_id: int,
        gift_type: str,
        description: str,
        is_anonymous: bool = False,
        urgency: str = "normal",
        expires_at: datetime,
    ) -> Optional[int]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO wish_requests (
                        requester_user_id, is_anonymous, gift_type, description,
                        urgency, status, expires_at
                    )
                    VALUES ($1, $2, $3, $4, $5, 'pending_moderation', $6)
                    RETURNING id
                    """,
                    requester_user_id,
                    is_anonymous,
                    gift_type,
                    description,
                    urgency,
                    expires_at,
                )
                wish_id = int(row["id"])
                await self._wish_log_event(
                    conn, wish_id, requester_user_id, "created", None
                )
                return wish_id
        except Exception as e:
            logger.error("wish_create failed: %s", e)
            return None

    async def wish_get(self, wish_id: int) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM wish_requests WHERE id = $1",
                    wish_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("wish_get failed id=%s: %s", wish_id, e)
            return None

    async def wish_count_active_for_requester(self, user_id: int) -> int:
        try:
            async with self.get_connection() as conn:
                return int(
                    await conn.fetchval(
                        """
                        SELECT COUNT(*) FROM wish_requests
                        WHERE requester_user_id = $1
                          AND status = ANY($2::varchar[])
                        """,
                        user_id,
                        list(ACTIVE_REQUESTER_STATUSES),
                    )
                    or 0
                )
        except Exception as e:
            logger.error("wish_count_active_for_requester uid=%s: %s", user_id, e)
            return 0

    async def wish_count_taken_by_donor(self, donor_id: int) -> int:
        try:
            async with self.get_connection() as conn:
                return int(
                    await conn.fetchval(
                        """
                        SELECT COUNT(*) FROM wish_requests
                        WHERE donor_user_id = $1 AND status = $2
                        """,
                        donor_id,
                        TAKEN_STATUS,
                    )
                    or 0
                )
        except Exception as e:
            logger.error("wish_count_taken_by_donor uid=%s: %s", donor_id, e)
            return 0

    async def wish_list_open(
        self, *, limit: int = 20, offset: int = 0
    ) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM wish_requests
                    WHERE status = 'open'
                      AND expires_at > NOW()
                    ORDER BY created_at ASC
                    LIMIT $1 OFFSET $2
                    """,
                    limit,
                    offset,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("wish_list_open failed: %s", e)
            return []

    async def wish_list_by_requester(
        self, user_id: int, *, limit: int = 10
    ) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM wish_requests
                    WHERE requester_user_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    user_id,
                    limit,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("wish_list_by_requester uid=%s: %s", user_id, e)
            return []

    async def wish_list_by_donor(
        self,
        donor_id: int,
        *,
        scope: str = "active",
        limit: int = 15,
    ) -> List[Dict[str, Any]]:
        if scope == "done":
            statuses = ("completed",)
        else:
            statuses = ("taken", "done_pending")
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM wish_requests
                    WHERE donor_user_id = $1
                      AND status = ANY($2::varchar[])
                    ORDER BY updated_at DESC
                    LIMIT $3
                    """,
                    donor_id,
                    list(statuses),
                    limit,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("wish_list_by_donor uid=%s: %s", donor_id, e)
            return []

    async def wish_set_digest_notice_message_id(
        self, wish_id: int, message_id: int
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE wish_requests
                    SET digest_notice_message_id = $2, updated_at = NOW()
                    WHERE id = $1
                    """,
                    wish_id,
                    message_id,
                )
                return True
        except Exception as e:
            logger.error("wish_set_digest_notice_message_id id=%s: %s", wish_id, e)
            return False

    async def wish_set_admin_notice_message_id(
        self, wish_id: int, message_id: int
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE wish_requests
                    SET admin_notice_message_id = $2, updated_at = NOW()
                    WHERE id = $1
                    """,
                    wish_id,
                    message_id,
                )
                return True
        except Exception as e:
            logger.error("wish_set_admin_notice_message_id id=%s: %s", wish_id, e)
            return False

    async def wish_set_button_title(self, wish_id: int, title: str) -> bool:
        try:
            t = (title or "").strip()[:64]
            if not t:
                return False
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE wish_requests
                    SET button_title = $2, updated_at = NOW()
                    WHERE id = $1
                    """,
                    wish_id,
                    t,
                )
                return True
        except Exception as e:
            logger.error("wish_set_button_title id=%s: %s", wish_id, e)
            return False

    async def wish_approve(
        self, wish_id: int, moderator_id: int
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE wish_requests
                    SET status = 'open',
                        moderator_user_id = $2,
                        updated_at = NOW()
                    WHERE id = $1 AND status = 'pending_moderation'
                    RETURNING *
                    """,
                    wish_id,
                    moderator_id,
                )
                if not row:
                    return None
                await self._wish_log_event(
                    conn, wish_id, moderator_id, "approved", None
                )
                return dict(row)
        except Exception as e:
            logger.error("wish_approve id=%s: %s", wish_id, e)
            return None

    async def wish_reject(
        self, wish_id: int, moderator_id: int, reason: str
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE wish_requests
                    SET status = 'rejected',
                        moderator_user_id = $2,
                        reject_reason = $3,
                        updated_at = NOW()
                    WHERE id = $1 AND status = 'pending_moderation'
                    RETURNING *
                    """,
                    wish_id,
                    moderator_id,
                    reason,
                )
                if not row:
                    return None
                await self._wish_log_event(
                    conn,
                    wish_id,
                    moderator_id,
                    "rejected",
                    {"reason": reason},
                )
                return dict(row)
        except Exception as e:
            logger.error("wish_reject id=%s: %s", wish_id, e)
            return None

    async def wish_take(
        self, wish_id: int, donor_id: int
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE wish_requests
                    SET status = 'taken',
                        donor_user_id = $2,
                        taken_at = NOW(),
                        updated_at = NOW()
                    WHERE id = $1
                      AND status = 'open'
                      AND requester_user_id <> $2
                      AND expires_at > NOW()
                    RETURNING *
                    """,
                    wish_id,
                    donor_id,
                )
                if not row:
                    return None
                await self._wish_log_event(
                    conn, wish_id, donor_id, "taken", None
                )
                return dict(row)
        except Exception as e:
            logger.error("wish_take id=%s donor=%s: %s", wish_id, donor_id, e)
            return None

    async def wish_dispute(
        self, wish_id: int, requester_id: int
    ) -> Optional[Dict[str, Any]]:
        """Автор просьбы сообщает о проблеме после «помощь оказана» → снова в пул."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE wish_requests
                    SET status = 'open',
                        donor_user_id = NULL,
                        taken_at = NULL,
                        updated_at = NOW()
                    WHERE id = $1
                      AND status = 'done_pending'
                      AND requester_user_id = $2
                    RETURNING *
                    """,
                    wish_id,
                    requester_id,
                )
                if not row:
                    return None
                await self._wish_log_event(
                    conn, wish_id, requester_id, "disputed", None
                )
                return dict(row)
        except Exception as e:
            logger.error("wish_dispute id=%s: %s", wish_id, e)
            return None

    async def wish_complete_subscription_gift(
        self, donor_id: int, recipient_id: int
    ) -> Optional[Dict[str, Any]]:
        """Автозавершение просьбы о продлении после оплаты подарка."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE wish_requests
                    SET status = 'completed',
                        confirmed_at = NOW(),
                        completed_at = NOW(),
                        updated_at = NOW()
                    WHERE donor_user_id = $1
                      AND requester_user_id = $2
                      AND gift_type = 'subscription'
                      AND status = 'taken'
                    RETURNING *
                    """,
                    donor_id,
                    recipient_id,
                )
                if not row:
                    return None
                wish_id = int(row["id"])
                await conn.execute(
                    """
                    INSERT INTO user_generosity_stats (user_id)
                    VALUES ($1)
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    donor_id,
                )
                await conn.execute(
                    """
                    UPDATE user_generosity_stats
                    SET wishes_completed_as_donor = wishes_completed_as_donor + 1,
                        updated_at = NOW()
                    WHERE user_id = $1
                    """,
                    donor_id,
                )
                await conn.execute(
                    """
                    INSERT INTO user_generosity_stats (user_id)
                    VALUES ($1)
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    recipient_id,
                )
                await conn.execute(
                    """
                    UPDATE user_generosity_stats
                    SET wishes_completed_as_requester = wishes_completed_as_requester + 1,
                        updated_at = NOW()
                    WHERE user_id = $1
                    """,
                    recipient_id,
                )
                await self._wish_log_event(
                    conn, wish_id, donor_id, "completed", {"auto": "subscription_gift"}
                )
                return dict(row)
        except Exception as e:
            logger.error(
                "wish_complete_subscription_gift donor=%s recipient=%s: %s",
                donor_id,
                recipient_id,
                e,
            )
            return None

    async def generosity_leaderboard(
        self, *, limit: int = 15
    ) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT wishes_completed_as_donor,
                           rating_sum,
                           rating_count
                    FROM user_generosity_stats
                    WHERE wishes_completed_as_donor > 0
                       OR rating_count > 0
                    ORDER BY
                        CASE WHEN rating_count > 0
                             THEN rating_sum::float / rating_count
                             ELSE 0 END DESC,
                        rating_count DESC,
                        wishes_completed_as_donor DESC
                    LIMIT $1
                    """,
                    limit,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("generosity_leaderboard failed: %s", e)
            return []

    async def wish_release(
        self, wish_id: int, actor_id: int
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE wish_requests
                    SET status = 'open',
                        donor_user_id = NULL,
                        taken_at = NULL,
                        updated_at = NOW()
                    WHERE id = $1
                      AND status = 'taken'
                      AND (donor_user_id = $2 OR requester_user_id = $2)
                    RETURNING *
                    """,
                    wish_id,
                    actor_id,
                )
                if not row:
                    return None
                await self._wish_log_event(
                    conn, wish_id, actor_id, "released", None
                )
                return dict(row)
        except Exception as e:
            logger.error("wish_release id=%s: %s", wish_id, e)
            return None

    async def wish_mark_done(
        self, wish_id: int, donor_id: int
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE wish_requests
                    SET status = 'done_pending',
                        updated_at = NOW()
                    WHERE id = $1
                      AND status = 'taken'
                      AND donor_user_id = $2
                    RETURNING *
                    """,
                    wish_id,
                    donor_id,
                )
                if not row:
                    return None
                await self._wish_log_event(
                    conn, wish_id, donor_id, "done_pending", None
                )
                return dict(row)
        except Exception as e:
            logger.error("wish_mark_done id=%s: %s", wish_id, e)
            return None

    async def wish_confirm(
        self, wish_id: int, requester_id: int
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE wish_requests
                    SET status = 'completed',
                        confirmed_at = NOW(),
                        completed_at = NOW(),
                        updated_at = NOW()
                    WHERE id = $1
                      AND status = 'done_pending'
                      AND requester_user_id = $2
                    RETURNING *
                    """,
                    wish_id,
                    requester_id,
                )
                if not row:
                    return None
                donor_id = row["donor_user_id"]
                if donor_id:
                    await conn.execute(
                        """
                        INSERT INTO user_generosity_stats (user_id)
                        VALUES ($1)
                        ON CONFLICT (user_id) DO NOTHING
                        """,
                        donor_id,
                    )
                    await conn.execute(
                        """
                        UPDATE user_generosity_stats
                        SET wishes_completed_as_donor = wishes_completed_as_donor + 1,
                            updated_at = NOW()
                        WHERE user_id = $1
                        """,
                        donor_id,
                    )
                    await conn.execute(
                        """
                        INSERT INTO user_generosity_stats (user_id)
                        VALUES ($1)
                        ON CONFLICT (user_id) DO NOTHING
                        """,
                        requester_id,
                    )
                    await conn.execute(
                        """
                        UPDATE user_generosity_stats
                        SET wishes_completed_as_requester = wishes_completed_as_requester + 1,
                            updated_at = NOW()
                        WHERE user_id = $1
                        """,
                        requester_id,
                    )
                await self._wish_log_event(
                    conn, wish_id, requester_id, "completed", None
                )
                return dict(row)
        except Exception as e:
            logger.error("wish_confirm id=%s: %s", wish_id, e)
            return None

    async def wish_rate_donor(
        self, wish_id: int, requester_id: int, rating: int
    ) -> bool:
        if rating < 1 or rating > 5:
            return False
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE wish_requests
                    SET donor_rating = $3, updated_at = NOW()
                    WHERE id = $1
                      AND requester_user_id = $2
                      AND status = 'completed'
                      AND donor_rating IS NULL
                      AND donor_user_id IS NOT NULL
                    RETURNING donor_user_id
                    """,
                    wish_id,
                    requester_id,
                    rating,
                )
                if not row:
                    return False
                donor_id = row["donor_user_id"]
                await conn.execute(
                    """
                    INSERT INTO user_generosity_stats (user_id)
                    VALUES ($1)
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    donor_id,
                )
                await conn.execute(
                    """
                    UPDATE user_generosity_stats
                    SET rating_sum = rating_sum + $2,
                        rating_count = rating_count + 1,
                        updated_at = NOW()
                    WHERE user_id = $1
                    """,
                    donor_id,
                    rating,
                )
                await self._wish_log_event(
                    conn,
                    wish_id,
                    requester_id,
                    "rated",
                    {"rating": rating},
                )
                return True
        except Exception as e:
            logger.error("wish_rate_donor id=%s: %s", wish_id, e)
            return False

    async def wish_cancel(
        self, wish_id: int, requester_id: int
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE wish_requests
                    SET status = 'cancelled', updated_at = NOW()
                    WHERE id = $1
                      AND requester_user_id = $2
                      AND status IN ('pending_moderation', 'open', 'taken')
                    RETURNING *
                    """,
                    wish_id,
                    requester_id,
                )
                if not row:
                    return None
                await self._wish_log_event(
                    conn, wish_id, requester_id, "cancelled", None
                )
                return dict(row)
        except Exception as e:
            logger.error("wish_cancel id=%s: %s", wish_id, e)
            return None

    async def wish_expire_open(self) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    UPDATE wish_requests
                    SET status = 'expired', updated_at = NOW()
                    WHERE status = 'open' AND expires_at <= NOW()
                    RETURNING *
                    """
                )
                for row in rows:
                    await self._wish_log_event(
                        conn, int(row["id"]), None, "expired", None
                    )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("wish_expire_open failed: %s", e)
            return []

    async def wish_release_stale_taken(
        self, timeout_days: int
    ) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    UPDATE wish_requests
                    SET status = 'open',
                        donor_user_id = NULL,
                        taken_at = NULL,
                        updated_at = NOW()
                    WHERE status = 'taken'
                      AND taken_at IS NOT NULL
                      AND taken_at <= NOW() - ($1::text || ' days')::interval
                    RETURNING *
                    """,
                    str(timeout_days),
                )
                for row in rows:
                    await self._wish_log_event(
                        conn,
                        int(row["id"]),
                        None,
                        "taken_timeout",
                        {"timeout_days": timeout_days},
                    )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("wish_release_stale_taken failed: %s", e)
            return []

    async def wish_list_open_for_digest_since(
        self, since: datetime
    ) -> List[Dict[str, Any]]:
        """Открытые просьбы, одобренные с ``since``, ещё не опубликованные в топике клуба."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT w.*
                    FROM wish_requests w
                    INNER JOIN wish_events e ON e.wish_id = w.id
                    WHERE e.event_type = 'approved'
                      AND e.created_at >= $1
                      AND w.status = 'open'
                      AND w.digest_notice_message_id IS NULL
                    ORDER BY e.created_at ASC
                    """,
                    since,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("wish_list_open_for_digest_since failed: %s", e)
            return []

    async def wish_list_approved_since(
        self, since: datetime
    ) -> List[Dict[str, Any]]:
        """Deprecated alias — используйте ``wish_list_open_for_digest_since``."""
        return await self.wish_list_open_for_digest_since(since)

    async def wish_list_open_for_group_reminder(
        self,
        *,
        open_days: int,
        reminder_gap_days: int,
        max_reminders: int,
    ) -> List[Dict[str, Any]]:
        """Открытые просьбы для повторного поста в группу (напоминание ангелу)."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    WITH enriched AS (
                        SELECT
                            w.*,
                            COALESCE(
                                (
                                    SELECT MAX(e.created_at)
                                    FROM wish_events e
                                    WHERE e.wish_id = w.id
                                      AND e.event_type IN (
                                          'approved', 'released', 'disputed'
                                      )
                                ),
                                w.created_at
                            ) AS pool_open_at,
                            (
                                SELECT COUNT(*)::int
                                FROM wish_events e
                                WHERE e.wish_id = w.id
                                  AND e.event_type = 'group_reminder'
                            ) AS reminder_count,
                            (
                                SELECT MAX(e.created_at)
                                FROM wish_events e
                                WHERE e.wish_id = w.id
                                  AND e.event_type = 'group_reminder'
                            ) AS last_reminder_at
                        FROM wish_requests w
                        WHERE w.status = 'open'
                          AND w.expires_at > NOW()
                    )
                    SELECT *
                    FROM enriched
                    WHERE pool_open_at <= NOW() - ($1::text || ' days')::interval
                      AND reminder_count < $2
                      AND (
                          last_reminder_at IS NULL
                          OR last_reminder_at <= NOW() - ($3::text || ' days')::interval
                      )
                    ORDER BY pool_open_at ASC
                    """,
                    str(open_days),
                    int(max_reminders),
                    str(reminder_gap_days),
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("wish_list_open_for_group_reminder failed: %s", e)
            return []

    async def wish_record_group_reminder(self, wish_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                await self._wish_log_event(
                    conn, wish_id, None, "group_reminder", None
                )
                await conn.execute(
                    "UPDATE wish_requests SET updated_at = NOW() WHERE id = $1",
                    wish_id,
                )
                return True
        except Exception as e:
            logger.error("wish_record_group_reminder id=%s: %s", wish_id, e)
            return False

    async def generosity_stats(self, user_id: int) -> Dict[str, Any]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM user_generosity_stats WHERE user_id = $1",
                    user_id,
                )
                if not row:
                    return {
                        "user_id": user_id,
                        "wishes_completed_as_donor": 0,
                        "wishes_completed_as_requester": 0,
                        "rating_sum": 0,
                        "rating_count": 0,
                    }
                return dict(row)
        except Exception as e:
            logger.error("generosity_stats uid=%s: %s", user_id, e)
            return {
                "user_id": user_id,
                "wishes_completed_as_donor": 0,
                "wishes_completed_as_requester": 0,
                "rating_sum": 0,
                "rating_count": 0,
            }

    async def _wish_log_event(
        self,
        conn,
        wish_id: int,
        actor_user_id: Optional[int],
        event_type: str,
        meta: Optional[Dict[str, Any]],
    ) -> None:
        await conn.execute(
            """
            INSERT INTO wish_events (wish_id, actor_user_id, event_type, meta)
            VALUES ($1, $2, $3, $4::jsonb)
            """,
            wish_id,
            actor_user_id,
            event_type,
            json.dumps(meta) if meta is not None else None,
        )
