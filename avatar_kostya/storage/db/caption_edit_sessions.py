"""Сессии итеративной редактуры caption по reply админа."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class CaptionEditSessionsMixin:
    async def create_caption_edit_session(
        self,
        *,
        entity_type: str,
        chat_id: int,
        root_message_id: int,
        caption_html: str,
        title: str = "",
        description: str = "",
        media_kind: str = "",
        topic_id: int = 0,
        pending_id: Optional[uuid.UUID] = None,
        meeting_id: str = "",
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[uuid.UUID]:
        sid = uuid.uuid4()
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO caption_edit_sessions (
                        id, entity_type, pending_id, meeting_id,
                        chat_id, topic_id, root_message_id, current_message_id,
                        media_kind, title, description, caption_html,
                        context_json, iterations_json
                    ) VALUES (
                        $1, $2, $3, $4,
                        $5, $6, $7, $7,
                        $8, $9, $10, $11,
                        $12::jsonb, '[]'::jsonb
                    )
                    ON CONFLICT (chat_id, root_message_id) DO UPDATE SET
                        entity_type = EXCLUDED.entity_type,
                        pending_id = EXCLUDED.pending_id,
                        meeting_id = EXCLUDED.meeting_id,
                        topic_id = EXCLUDED.topic_id,
                        current_message_id = EXCLUDED.current_message_id,
                        media_kind = EXCLUDED.media_kind,
                        title = EXCLUDED.title,
                        description = EXCLUDED.description,
                        caption_html = EXCLUDED.caption_html,
                        context_json = EXCLUDED.context_json,
                        updated_at = NOW()
                    """,
                    sid,
                    (entity_type or "").strip(),
                    pending_id,
                    (meeting_id or "")[:64],
                    int(chat_id),
                    int(topic_id or 0),
                    int(root_message_id),
                    (media_kind or "")[:32],
                    (title or "")[:500],
                    (description or "")[:4000],
                    (caption_html or "")[:8000],
                    json.dumps(context or {}, ensure_ascii=False),
                )
            return sid
        except Exception as e:
            logger.error("create_caption_edit_session: %s", e, exc_info=True)
            return None

    async def get_caption_edit_session_by_root(
        self, chat_id: int, root_message_id: int
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM caption_edit_sessions
                    WHERE chat_id = $1 AND root_message_id = $2
                    """,
                    int(chat_id),
                    int(root_message_id),
                )
            return self._row_to_session(row)
        except Exception as e:
            logger.error("get_caption_edit_session_by_root: %s", e)
            return None

    async def get_caption_edit_session_by_any_message(
        self, chat_id: int, message_id: int
    ) -> Optional[Dict[str, Any]]:
        """Ищем сессию по root или по текущему message_id (после resend)."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM caption_edit_sessions
                    WHERE chat_id = $1
                      AND (root_message_id = $2 OR current_message_id = $2)
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    int(chat_id),
                    int(message_id),
                )
            return self._row_to_session(row)
        except Exception as e:
            logger.error("get_caption_edit_session_by_any_message: %s", e)
            return None

    async def append_caption_edit_iteration(
        self,
        session_id: uuid.UUID,
        *,
        role: str,
        content: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        caption_html: Optional[str] = None,
        current_message_id: Optional[int] = None,
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT iterations_json FROM caption_edit_sessions WHERE id = $1",
                    session_id,
                )
                if not row:
                    return False
                iters = row["iterations_json"]
                if isinstance(iters, str):
                    try:
                        iters = json.loads(iters)
                    except json.JSONDecodeError:
                        iters = []
                if not isinstance(iters, list):
                    iters = []
                iters.append(
                    {
                        "role": role,
                        "content": (content or "")[:8000],
                        "at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                sets = ["iterations_json = $2::jsonb", "updated_at = NOW()"]
                params: List[Any] = [
                    session_id,
                    json.dumps(iters, ensure_ascii=False),
                ]
                idx = 3
                if title is not None:
                    sets.append(f"title = ${idx}")
                    params.append(title[:500])
                    idx += 1
                if description is not None:
                    sets.append(f"description = ${idx}")
                    params.append(description[:4000])
                    idx += 1
                if caption_html is not None:
                    sets.append(f"caption_html = ${idx}")
                    params.append(caption_html[:8000])
                    idx += 1
                if current_message_id is not None:
                    sets.append(f"current_message_id = ${idx}")
                    params.append(int(current_message_id))
                    idx += 1
                await conn.execute(
                    f"UPDATE caption_edit_sessions SET {', '.join(sets)} WHERE id = $1",
                    *params,
                )
            return True
        except Exception as e:
            logger.error("append_caption_edit_iteration: %s", e, exc_info=True)
            return False

    @staticmethod
    def _row_to_session(row) -> Optional[Dict[str, Any]]:
        if not row:
            return None
        d = dict(row)
        for key in ("context_json", "iterations_json"):
            val = d.get(key)
            if isinstance(val, str):
                try:
                    d[key] = json.loads(val)
                except json.JSONDecodeError:
                    d[key] = {} if key == "context_json" else []
            elif val is None:
                d[key] = {} if key == "context_json" else []
        return d
