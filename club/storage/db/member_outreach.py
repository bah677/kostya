"""Mixin: состояние клубных проактивных рассылок в личку."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MemberOutreachMixin:
    async def ensure_outreach_state(self, user_id: int) -> Dict[str, Any]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO member_outreach_state (user_id)
                    VALUES ($1)
                    ON CONFLICT (user_id) DO NOTHING
                    RETURNING *
                    """,
                    user_id,
                )
                if row:
                    return dict(row)
                row = await conn.fetchrow(
                    "SELECT * FROM member_outreach_state WHERE user_id = $1",
                    user_id,
                )
                return dict(row) if row else {"user_id": user_id}
        except Exception as e:
            logger.error("ensure_outreach_state uid=%s: %s", user_id, e)
            return {"user_id": user_id}

    async def get_outreach_state(self, user_id: int) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM member_outreach_state WHERE user_id = $1",
                    user_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_outreach_state uid=%s: %s", user_id, e)
            return None

    async def set_pilot_cohort(self, user_ids: List[int], *, pilot: bool = True) -> None:
        if not user_ids:
            return
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "UPDATE member_outreach_state SET pilot_cohort = FALSE"
                )
                await conn.executemany(
                    """
                    INSERT INTO member_outreach_state (user_id, pilot_cohort, updated_at)
                    VALUES ($1, $2, NOW())
                    ON CONFLICT (user_id) DO UPDATE
                    SET pilot_cohort = EXCLUDED.pilot_cohort, updated_at = NOW()
                    """,
                    [(uid, pilot) for uid in user_ids],
                )
        except Exception as e:
            logger.error("set_pilot_cohort: %s", e)

    async def list_pilot_outreach_user_ids(self) -> List[int]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT user_id FROM member_outreach_state
                    WHERE pilot_cohort = TRUE
                    ORDER BY user_id
                    """
                )
                return [int(r["user_id"]) for r in rows]
        except Exception as e:
            logger.error("list_pilot_outreach_user_ids: %s", e)
            return []

    async def fetch_top_group_active_user_ids(
        self,
        club_group_id: int,
        *,
        limit: int = 30,
        lookback_days: int = 30,
    ) -> List[int]:
        """Топ участников по сообщениям в клубной группе с активной лицензией."""
        if not club_group_id:
            return []
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT m.user_id, COUNT(*) AS msg_cnt
                    FROM messages m
                    INNER JOIN license l ON l.user_id = m.user_id
                        AND l.status = 'active'
                        AND l.expires_at > NOW()
                    WHERE m.chat_id = $1
                      AND m.role = 'user'
                      AND m.deleted_at IS NULL
                      AND COALESCE(TRIM(m.content), '') <> ''
                      AND m.created_at > NOW() - make_interval(days => $2)
                    GROUP BY m.user_id
                    ORDER BY msg_cnt DESC
                    LIMIT $3
                    """,
                    club_group_id,
                    lookback_days,
                    limit,
                )
                return [int(r["user_id"]) for r in rows]
        except Exception as e:
            logger.error("fetch_top_group_active_user_ids: %s", e)
            return []

    async def get_proactive_sent_count_today(
        self, user_id: int, *, today: Optional[date] = None
    ) -> int:
        state = await self.get_outreach_state(user_id)
        if not state:
            return 0
        sent_date = state.get("proactive_sent_date")
        if sent_date is None:
            return 0
        ref = today or date.today()
        if sent_date != ref:
            return 0
        return int(state.get("proactive_sent_count") or 0)

    async def increment_proactive_sent_today(self, user_id: int) -> int:
        await self.ensure_outreach_state(user_id)
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE member_outreach_state
                    SET proactive_sent_date = CURRENT_DATE,
                        proactive_sent_count = CASE
                            WHEN proactive_sent_date = CURRENT_DATE
                            THEN proactive_sent_count + 1
                            ELSE 1
                        END,
                        updated_at = NOW()
                    WHERE user_id = $1
                    RETURNING proactive_sent_count
                    """,
                    user_id,
                )
                return int(row["proactive_sent_count"]) if row else 1
        except Exception as e:
            logger.error("increment_proactive_sent_today uid=%s: %s", user_id, e)
            return 1

    async def set_outreach_paused(
        self, user_id: int, until: datetime, *, bump_complaint: bool = False
    ) -> None:
        await self.ensure_outreach_state(user_id)
        try:
            async with self.get_connection() as conn:
                if bump_complaint:
                    await conn.execute(
                        """
                        UPDATE member_outreach_state
                        SET outreach_paused_until = $2,
                            complaints_detected = complaints_detected + 1,
                            suppression_level = LEAST(suppression_level + 1, 5),
                            last_complaint_at = NOW(),
                            updated_at = NOW()
                        WHERE user_id = $1
                        """,
                        user_id,
                        until,
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE member_outreach_state
                        SET outreach_paused_until = $2, updated_at = NOW()
                        WHERE user_id = $1
                        """,
                        user_id,
                        until,
                    )
        except Exception as e:
            logger.error("set_outreach_paused uid=%s: %s", user_id, e)

    async def touch_outreach_dm_sent(
        self, user_id: int, *, kind: str
    ) -> None:
        col = {
            "digest": "last_digest_dm_at",
            "scripture": "last_scripture_dm_at",
        }.get(kind)
        if not col:
            return
        await self.ensure_outreach_state(user_id)
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    f"""
                    UPDATE member_outreach_state
                    SET {col} = NOW(), updated_at = NOW()
                    WHERE user_id = $1
                    """,
                    user_id,
                )
        except Exception as e:
            logger.error("touch_outreach_dm_sent uid=%s: %s", user_id, e)

    async def user_recent_private_messages(
        self, user_id: int, *, limit: int = 12
    ) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT role, content, created_at
                    FROM messages
                    WHERE user_id = $1
                      AND chat_type = 'private'
                      AND deleted_at IS NULL
                      AND COALESCE(TRIM(content), '') <> ''
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    user_id,
                    limit,
                )
                return [dict(r) for r in reversed(rows)]
        except Exception as e:
            logger.error("user_recent_private_messages uid=%s: %s", user_id, e)
            return []
