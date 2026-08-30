"""Отложенные ответы после сбоя генерации (DEV-6)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PendingReplyMixin:
    async def enqueue_pending_reply(
        self,
        *,
        user_id: int,
        chat_id: int,
        text: str,
    ) -> Optional[int]:
        try:
            async with self.get_connection() as conn:
                row_id = await conn.fetchval(
                    """
                    INSERT INTO pending_reply (user_id, chat_id, text, status)
                    VALUES ($1, $2, $3, 'pending')
                    RETURNING id
                    """,
                    int(user_id),
                    int(chat_id),
                    text or "",
                )
                return int(row_id) if row_id is not None else None
        except Exception as e:
            logger.error("enqueue_pending_reply uid=%s: %s", user_id, e)
            return None

    async def fetch_pending_replies(
        self, *, limit: int = 50, max_age_hours: int = 6
    ) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, user_id, chat_id, text, attempts, created_at
                    FROM pending_reply
                    WHERE status = 'pending'
                      AND created_at > NOW() - ($2::text || ' hours')::interval
                    ORDER BY created_at ASC
                    LIMIT $1
                    """,
                    int(limit),
                    str(int(max_age_hours)),
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("fetch_pending_replies: %s", e)
            return []

    async def mark_pending_reply_done(self, row_id: int) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE pending_reply
                       SET status = 'done', updated_at = NOW()
                     WHERE id = $1
                    """,
                    int(row_id),
                )
        except Exception as e:
            logger.error("mark_pending_reply_done id=%s: %s", row_id, e)

    async def mark_pending_reply_attempt(
        self, row_id: int, *, error: str = ""
    ) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE pending_reply
                       SET attempts = attempts + 1,
                           last_error = $2,
                           updated_at = NOW()
                     WHERE id = $1
                    """,
                    int(row_id),
                    (error or "")[:500],
                )
        except Exception as e:
            logger.error("mark_pending_reply_attempt id=%s: %s", row_id, e)

    async def fail_stale_pending_replies(self, *, max_age_hours: int = 6) -> List[Dict[str, Any]]:
        """Помечает просроченные pending → failed, возвращает затронутые."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    UPDATE pending_reply
                       SET status = 'failed',
                           failed_at = NOW(),
                           updated_at = NOW()
                     WHERE status = 'pending'
                       AND created_at <= NOW() - ($1::text || ' hours')::interval
                    RETURNING id, user_id
                    """,
                    str(int(max_age_hours)),
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("fail_stale_pending_replies: %s", e)
            return []

    async def pause_all_pending_replies(self) -> int:
        try:
            async with self.get_connection() as conn:
                status = await conn.execute(
                    """
                    UPDATE pending_reply
                       SET status = 'paused', updated_at = NOW()
                     WHERE status = 'pending'
                    """
                )
                # asyncpg: "UPDATE N"
                try:
                    return int(str(status).split()[-1])
                except Exception:
                    return 0
        except Exception as e:
            logger.error("pause_all_pending_replies: %s", e)
            return 0

    async def resume_paused_pending_replies(self) -> int:
        try:
            async with self.get_connection() as conn:
                status = await conn.execute(
                    """
                    UPDATE pending_reply
                       SET status = 'pending', updated_at = NOW()
                     WHERE status = 'paused'
                    """
                )
                try:
                    return int(str(status).split()[-1])
                except Exception:
                    return 0
        except Exception as e:
            logger.error("resume_paused_pending_replies: %s", e)
            return 0
