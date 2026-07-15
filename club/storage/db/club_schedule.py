"""
Mixin: расписание клуба (`club_schedule_events`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ClubScheduleMixin:

    async def insert_club_schedule_event(
        self,
        *,
        starts_at: datetime,
        title: str,
        content_type: str = "other",
        ends_at: Optional[datetime] = None,
        source: str = "group_message",
        source_message_id: Optional[int] = None,
        source_chat_id: Optional[int] = None,
        source_admin_id: Optional[int] = None,
        group_message_link: Optional[str] = None,
        raw_text: Optional[str] = None,
        confidence: float = 1.0,
    ) -> Optional[int]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO club_schedule_events (
                        starts_at, ends_at, title, content_type, source,
                        source_message_id, source_chat_id, source_admin_id,
                        group_message_link, raw_text, confidence
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    RETURNING id
                    """,
                    starts_at,
                    ends_at,
                    (title or "").strip()[:500],
                    (content_type or "other").strip()[:32],
                    (source or "group_message").strip()[:16],
                    source_message_id,
                    source_chat_id,
                    source_admin_id,
                    group_message_link,
                    raw_text,
                    float(confidence),
                )
                return int(row["id"]) if row else None
        except Exception as e:
            logger.error("insert_club_schedule_event: %s", e)
            return None

    async def cancel_club_schedule_near(
        self,
        *,
        starts_at: datetime,
        title_hint: str = "",
        window_minutes: int = 120,
    ) -> int:
        """Помечает отменёнными события в окне ±window вокруг starts_at."""
        hint = (title_hint or "").strip().lower()
        try:
            async with self.get_connection() as conn:
                if hint:
                    result = await conn.execute(
                        """
                        UPDATE club_schedule_events
                        SET is_cancelled = TRUE, updated_at = NOW()
                        WHERE NOT is_cancelled
                          AND starts_at BETWEEN $1 AND $2
                          AND LOWER(title) LIKE '%' || $3 || '%'
                        """,
                        starts_at - timedelta(minutes=window_minutes),
                        starts_at + timedelta(minutes=window_minutes),
                        hint[:80],
                    )
                else:
                    result = await conn.execute(
                        """
                        UPDATE club_schedule_events
                        SET is_cancelled = TRUE, updated_at = NOW()
                        WHERE NOT is_cancelled
                          AND starts_at BETWEEN $1 AND $2
                        """,
                        starts_at - timedelta(minutes=window_minutes),
                        starts_at + timedelta(minutes=window_minutes),
                    )
                return int(result.split()[-1]) if result else 0
        except Exception as e:
            logger.error("cancel_club_schedule_near: %s", e)
            return 0

    async def list_club_schedule_events(
        self,
        *,
        from_at: datetime,
        to_at: datetime,
        include_cancelled: bool = False,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                if include_cancelled:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM club_schedule_events
                        WHERE starts_at >= $1 AND starts_at < $2
                        ORDER BY starts_at ASC
                        LIMIT $3
                        """,
                        from_at,
                        to_at,
                        limit,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM club_schedule_events
                        WHERE starts_at >= $1 AND starts_at < $2
                          AND NOT is_cancelled
                        ORDER BY starts_at ASC
                        LIMIT $3
                        """,
                        from_at,
                        to_at,
                        limit,
                    )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_club_schedule_events: %s", e)
            return []

    async def list_recent_club_schedule_raw(
        self, *, limit: int = 20
    ) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM club_schedule_events
                    ORDER BY created_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_recent_club_schedule_raw: %s", e)
            return []
