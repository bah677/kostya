"""Отложенный пуш второй молитвы (DEV-4)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_NUDGE_TEXT = (
    "Здравствуйте! Несколько дней назад вы обращались с просьбой о молитве"
    "{about}.\n\n"
    "Если эта тема ещё на сердце — или появилась новая — я рядом.\n"
    "Напишите /prayer или нажмите «🙏 Помолиться о своём»."
)


def clip_topic_snippet(text: str, *, max_len: int = 120) -> str:
    t = " ".join((text or "").split())
    if len(t) <= max_len:
        return t
    cut = t[: max_len + 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(".,;:") + "…"


def format_nudge_text(snippet: Optional[str]) -> str:
    sn = (snippet or "").strip()
    about = f" («{sn}»)" if sn else ""
    return _NUDGE_TEXT.format(about=about)


class SecondPrayerNudgeMixin:
    async def schedule_second_prayer_nudge(
        self,
        user_id: int,
        topic_snippet: str,
        *,
        delay_days: int = 4,
    ) -> bool:
        """После первой молитвы; ON CONFLICT DO NOTHING."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO second_prayer_nudge (user_id, due_at, topic_snippet)
                    VALUES ($1, NOW() + ($2::text || ' days')::interval, $3)
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    int(user_id),
                    str(int(delay_days)),
                    (topic_snippet or "")[:200],
                )
                return True
        except Exception as e:
            logger.error("schedule_second_prayer_nudge uid=%s: %s", user_id, e)
            return False

    async def count_sent_prayer_voices(self, user_id: int) -> int:
        """Завершённые отправленные аудио молитвы."""
        try:
            async with self.get_connection() as conn:
                n_log = await conn.fetchval(
                    """
                    SELECT COUNT(*)::int
                    FROM interaction_logs
                    WHERE user_id = $1
                      AND event_type = 'prayer_voice_sent'
                    """,
                    int(user_id),
                )
                n_quota = await conn.fetchval(
                    """
                    SELECT COUNT(*)::int
                    FROM prayer_voice_quota_log
                    WHERE user_id = $1
                    """,
                    int(user_id),
                )
                return max(int(n_log or 0), int(n_quota or 0))
        except Exception as e:
            logger.debug("count_sent_prayer_voices uid=%s: %s", user_id, e)
            return 0

    async def fetch_due_second_prayer_nudges(
        self, *, limit: int = 200
    ) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT n.user_id, n.due_at, n.topic_snippet,
                           n.crisis_postpone_count, n.created_at,
                           COALESCE(u.timezone_offset, 180) AS timezone_offset
                    FROM second_prayer_nudge n
                    LEFT JOIN users u ON u.user_id = n.user_id
                    WHERE n.sent_at IS NULL
                      AND n.due_at <= NOW()
                    ORDER BY n.due_at ASC
                    LIMIT $1
                    """,
                    int(limit),
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("fetch_due_second_prayer_nudges: %s", e)
            return []

    async def delete_second_prayer_nudge(self, user_id: int) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "DELETE FROM second_prayer_nudge WHERE user_id = $1",
                    int(user_id),
                )
        except Exception as e:
            logger.debug("delete_second_prayer_nudge uid=%s: %s", user_id, e)

    async def mark_second_prayer_nudge_sent(self, user_id: int) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE second_prayer_nudge
                       SET sent_at = NOW()
                     WHERE user_id = $1
                    """,
                    int(user_id),
                )
        except Exception as e:
            logger.error("mark_second_prayer_nudge_sent uid=%s: %s", user_id, e)

    async def postpone_second_prayer_nudge(
        self,
        user_id: int,
        *,
        days: int = 2,
        increment_crisis: bool = False,
        new_due_at: Optional[datetime] = None,
    ) -> None:
        try:
            async with self.get_connection() as conn:
                if new_due_at is not None:
                    await conn.execute(
                        """
                        UPDATE second_prayer_nudge
                           SET due_at = $2,
                               crisis_postpone_count = crisis_postpone_count
                                 + CASE WHEN $3 THEN 1 ELSE 0 END
                         WHERE user_id = $1
                        """,
                        int(user_id),
                        new_due_at,
                        bool(increment_crisis),
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE second_prayer_nudge
                           SET due_at = NOW() + ($2::text || ' days')::interval,
                               crisis_postpone_count = crisis_postpone_count
                                 + CASE WHEN $3 THEN 1 ELSE 0 END
                         WHERE user_id = $1
                        """,
                        int(user_id),
                        str(int(days)),
                        bool(increment_crisis),
                    )
        except Exception as e:
            logger.error("postpone_second_prayer_nudge uid=%s: %s", user_id, e)
