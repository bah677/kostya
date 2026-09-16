"""
Mixin: расписание клуба (`club_schedule_events`) + air_invite_sends / ops.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

INVITEABLE_CONTENT_TYPES = ("air", "qa", "repentance", "other")


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
        recurrence: str = "none",
        recurrence_dow: Optional[int] = None,
        recurrence_until: Optional[date] = None,
        series_id: Optional[int] = None,
    ) -> Optional[int]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO club_schedule_events (
                        starts_at, ends_at, title, content_type, source,
                        source_message_id, source_chat_id, source_admin_id,
                        group_message_link, raw_text, confidence,
                        recurrence, recurrence_dow, recurrence_until, series_id
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                        $12, $13, $14, $15
                    )
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
                    (recurrence or "none").strip()[:16],
                    recurrence_dow,
                    recurrence_until,
                    series_id,
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
                          AND recurrence = 'none'
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
                          AND recurrence = 'none'
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
        include_templates: bool = False,
        content_types: Optional[List[str]] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                clauses = ["starts_at >= $1", "starts_at < $2"]
                args: List[Any] = [from_at, to_at]
                if not include_cancelled:
                    clauses.append("NOT is_cancelled")
                if not include_templates:
                    clauses.append("recurrence = 'none'")
                if content_types:
                    args.append(list(content_types))
                    clauses.append(f"content_type = ANY(${len(args)}::text[])")
                args.append(limit)
                lim_i = len(args)
                sql = f"""
                    SELECT * FROM club_schedule_events
                    WHERE {' AND '.join(clauses)}
                    ORDER BY starts_at ASC
                    LIMIT ${lim_i}
                """
                rows = await conn.fetch(sql, *args)
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_club_schedule_events: %s", e)
            return []

    async def list_schedule_recurrence_templates(self) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM club_schedule_events
                    WHERE NOT is_cancelled
                      AND recurrence IN ('daily', 'weekly')
                    ORDER BY id ASC
                    """
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_schedule_recurrence_templates: %s", e)
            return []

    async def has_schedule_instance_on_day(
        self,
        *,
        series_id: int,
        day_start: datetime,
        day_end: datetime,
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT 1 FROM club_schedule_events
                    WHERE series_id = $1
                      AND starts_at >= $2 AND starts_at < $3
                      AND NOT is_cancelled
                      AND recurrence = 'none'
                    LIMIT 1
                    """,
                    series_id,
                    day_start,
                    day_end,
                )
                return row is not None
        except Exception as e:
            logger.error("has_schedule_instance_on_day: %s", e)
            return True

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

    async def get_club_schedule_event(self, event_id: int) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM club_schedule_events WHERE id = $1",
                    event_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_club_schedule_event: %s", e)
            return None

    async def set_schedule_recording_url(
        self, event_id: int, url: str
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE club_schedule_events
                    SET recording_url = $2,
                        recording_ready_at = NOW(),
                        updated_at = NOW()
                    WHERE id = $1
                    RETURNING *
                    """,
                    event_id,
                    (url or "").strip()[:2000],
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("set_schedule_recording_url: %s", e)
            return None

    async def mark_recording_requested(self, event_id: int) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE club_schedule_events
                    SET recording_requested_at = NOW(), updated_at = NOW()
                    WHERE id = $1
                    """,
                    event_id,
                )
        except Exception as e:
            logger.error("mark_recording_requested: %s", e)

    async def list_events_needing_recording_request(
        self, *, now: datetime, max_age_hours: int = 48
    ) -> List[Dict[str, Any]]:
        """Эфиры закончились 2+ часа назад, записи нет, запрос ещё не слали / пора повтор."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM club_schedule_events
                    WHERE NOT is_cancelled
                      AND recurrence = 'none'
                      AND content_type = ANY($1::text[])
                      AND recording_url IS NULL
                      AND COALESCE(ends_at, starts_at + INTERVAL '2 hours')
                          <= $2::timestamptz - INTERVAL '2 hours'
                      AND COALESCE(ends_at, starts_at + INTERVAL '2 hours')
                          >= $2::timestamptz - make_interval(hours => $3)
                      AND (
                        recording_requested_at IS NULL
                        OR recording_requested_at <= $2::timestamptz - INTERVAL '12 hours'
                      )
                    ORDER BY COALESCE(ends_at, starts_at) ASC
                    LIMIT 20
                    """,
                    list(INVITEABLE_CONTENT_TYPES),
                    now,
                    int(max_age_hours),
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_events_needing_recording_request: %s", e)
            return []

    async def try_claim_air_send(
        self, *, event_id: int, user_id: int, kind: str
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO air_invite_sends (event_id, user_id, kind)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (event_id, user_id, kind) DO NOTHING
                    RETURNING id
                    """,
                    event_id,
                    user_id,
                    kind,
                )
                return row is not None
        except Exception as e:
            logger.error("try_claim_air_send: %s", e)
            return False

    async def user_got_air_invite_today(self, user_id: int, *, day: date) -> bool:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT 1 FROM air_invite_sends
                    WHERE user_id = $1
                      AND kind = 'invite'
                      AND (sent_at AT TIME ZONE 'Europe/Moscow')::date = $2
                    LIMIT 1
                    """,
                    user_id,
                    day,
                )
                return row is not None
        except Exception as e:
            logger.error("user_got_air_invite_today: %s", e)
            return False

    async def list_air_invite_recipients(
        self, event_id: int, *, kind: str = "invite"
    ) -> List[int]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT user_id FROM air_invite_sends
                    WHERE event_id = $1 AND kind = $2
                    """,
                    event_id,
                    kind,
                )
                return [int(r["user_id"]) for r in rows]
        except Exception as e:
            logger.error("list_air_invite_recipients: %s", e)
            return []

    async def count_schedule_covered_days(
        self, *, from_day: date, days: int = 7
    ) -> int:
        """Сколько из ближайших `days` дней имеют хотя бы одно событие."""
        to_day = from_day + timedelta(days=max(0, days - 1))
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT COUNT(DISTINCT (starts_at AT TIME ZONE 'Europe/Moscow')::date) AS n
                    FROM club_schedule_events
                    WHERE NOT is_cancelled
                      AND recurrence = 'none'
                      AND (starts_at AT TIME ZONE 'Europe/Moscow')::date
                          BETWEEN $1 AND $2
                    """,
                    from_day,
                    to_day,
                )
                return int(row["n"] or 0) if row else 0
        except Exception as e:
            logger.error("count_schedule_covered_days: %s", e)
            return 0

    async def max_future_schedule_date(self) -> Optional[date]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT MAX((starts_at AT TIME ZONE 'Europe/Moscow')::date) AS d
                    FROM club_schedule_events
                    WHERE NOT is_cancelled
                      AND recurrence = 'none'
                      AND starts_at > NOW()
                    """
                )
                return row["d"] if row and row["d"] else None
        except Exception as e:
            logger.error("max_future_schedule_date: %s", e)
            return None

    async def count_inviteable_events_in_range(
        self, *, from_at: datetime, to_at: datetime
    ) -> int:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT COUNT(*)::int AS n
                    FROM club_schedule_events
                    WHERE NOT is_cancelled
                      AND recurrence = 'none'
                      AND content_type = ANY($3::text[])
                      AND starts_at >= $1 AND starts_at < $2
                    """,
                    from_at,
                    to_at,
                    list(INVITEABLE_CONTENT_TYPES),
                )
                return int(row["n"] or 0) if row else 0
        except Exception as e:
            logger.error("count_inviteable_events_in_range: %s", e)
            return 0

    async def count_group_air_mentions(
        self, *, club_group_id: int, since: datetime
    ) -> int:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT COUNT(*)::int AS n
                    FROM messages
                    WHERE chat_id = $1
                      AND created_at >= $2
                      AND deleted_at IS NULL
                      AND role = 'user'
                      AND (
                        content ILIKE '%эфир%'
                        OR content ILIKE '%эфире%'
                        OR content ILIKE '%эфира%'
                        OR content ILIKE '%эфиру%'
                      )
                    """,
                    club_group_id,
                    since,
                )
                return int(row["n"] or 0) if row else 0
        except Exception as e:
            logger.error("count_group_air_mentions: %s", e)
            return 0

    async def get_schedule_ops_state(self, key: str) -> Dict[str, Any]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT value FROM schedule_ops_state WHERE key = $1",
                    key,
                )
                if not row:
                    return {}
                val = row["value"]
                if isinstance(val, dict):
                    return val
                if isinstance(val, str):
                    return json.loads(val)
                return dict(val)
        except Exception as e:
            logger.error("get_schedule_ops_state: %s", e)
            return {}

    async def set_schedule_ops_state(self, key: str, value: Dict[str, Any]) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO schedule_ops_state (key, value, updated_at)
                    VALUES ($1, $2::jsonb, NOW())
                    ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value, updated_at = NOW()
                    """,
                    key,
                    json.dumps(value, ensure_ascii=False, default=str),
                )
        except Exception as e:
            logger.error("set_schedule_ops_state: %s", e)

    async def user_wrote_bot_since(
        self, user_id: int, *, since: datetime
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT 1 FROM messages
                    WHERE user_id = $1
                      AND chat_type = 'private'
                      AND role = 'user'
                      AND created_at >= $2
                      AND deleted_at IS NULL
                    LIMIT 1
                    """,
                    user_id,
                    since,
                )
                return row is not None
        except Exception as e:
            logger.error("user_wrote_bot_since: %s", e)
            return False

    async def user_wrote_group_since(
        self, user_id: int, *, club_group_id: int, since: datetime
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT 1 FROM messages
                    WHERE user_id = $1
                      AND chat_id = $2
                      AND role = 'user'
                      AND created_at >= $3
                      AND deleted_at IS NULL
                    LIMIT 1
                    """,
                    user_id,
                    club_group_id,
                    since,
                )
                return row is not None
        except Exception as e:
            logger.error("user_wrote_group_since: %s", e)
            return False
