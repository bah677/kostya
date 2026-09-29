"""RSVP на закрытые встречи встречающих."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class GreeterMeetingMixin:
    async def upsert_greeter_meeting_rsvp(
        self,
        *,
        meeting_key: str,
        user_id: int,
        response: str,
    ) -> bool:
        if response not in ("coming", "cant"):
            return False
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO greeter_meeting_rsvp
                        (meeting_key, user_id, response, responded_at)
                    VALUES ($1, $2, $3, NOW())
                    ON CONFLICT (meeting_key, user_id) DO UPDATE
                    SET response = EXCLUDED.response,
                        responded_at = NOW()
                    """,
                    meeting_key,
                    user_id,
                    response,
                )
                return True
        except Exception as e:
            logger.error("upsert_greeter_meeting_rsvp: %s", e, exc_info=True)
            return False

    async def get_greeter_meeting_rsvp(
        self, *, meeting_key: str, user_id: int
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT meeting_key, user_id, response, responded_at
                    FROM greeter_meeting_rsvp
                    WHERE meeting_key = $1 AND user_id = $2
                    """,
                    meeting_key,
                    user_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_greeter_meeting_rsvp: %s", e)
            return None

    async def list_greeter_meeting_rsvp(
        self, *, meeting_key: str
    ) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT r.user_id, r.response, r.responded_at,
                           u.first_name, u.username
                    FROM greeter_meeting_rsvp r
                    LEFT JOIN users u ON u.user_id = r.user_id
                    WHERE r.meeting_key = $1
                    ORDER BY r.responded_at ASC
                    """,
                    meeting_key,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_greeter_meeting_rsvp: %s", e)
            return []

    async def list_active_club_greeter_ids(self) -> List[int]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT user_id
                    FROM club_greeter
                    WHERE active = TRUE
                    ORDER BY user_id
                    """
                )
                return [int(r["user_id"]) for r in rows]
        except Exception as e:
            logger.error("list_active_club_greeter_ids: %s", e)
            return []
