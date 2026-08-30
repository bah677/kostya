"""Mixin: сценарии Reels + обратная связь."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ReelsScenariosMixin:
    async def insert_reels_scenario(
        self,
        *,
        pending_id: Optional[uuid.UUID],
        ref_code: str,
        air_title: str,
        idea_title: str,
        idea_score: float,
        anchor_sec: float,
        scenario_text: str,
        transcript_window: str,
        hook_alternatives: str = "",
        plan_json: Optional[dict] = None,
        rubric_json: Optional[dict] = None,
        chat_id: int = 0,
        message_id: int = 0,
    ) -> Optional[uuid.UUID]:
        sid = uuid.uuid4()
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO reels_scenarios (
                        id, pending_id, ref_code, air_title, idea_title,
                        idea_score, anchor_sec, scenario_text, transcript_window,
                        hook_alternatives, plan_json, rubric_json,
                        status, chat_id, message_id
                    ) VALUES (
                        $1,$2,$3,$4,$5,
                        $6,$7,$8,$9,
                        $10,$11::jsonb,$12::jsonb,
                        'draft',$13,$14
                    )
                    """,
                    sid,
                    pending_id,
                    ref_code,
                    (air_title or "")[:500],
                    (idea_title or "")[:500],
                    float(idea_score or 0),
                    float(anchor_sec or 0),
                    scenario_text or "",
                    transcript_window or "",
                    hook_alternatives or "",
                    json.dumps(plan_json or {}, ensure_ascii=False),
                    json.dumps(rubric_json or {}, ensure_ascii=False),
                    int(chat_id or 0),
                    int(message_id or 0),
                )
            return sid
        except Exception as e:
            logger.error("insert_reels_scenario: %s", e)
            return None

    async def update_reels_scenario_message(
        self, scenario_id: uuid.UUID, *, chat_id: int, message_id: int
    ) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE reels_scenarios
                    SET chat_id = $2, message_id = $3, updated_at = NOW()
                    WHERE id = $1
                    """,
                    scenario_id,
                    int(chat_id),
                    int(message_id),
                )
        except Exception as e:
            logger.error("update_reels_scenario_message: %s", e)

    async def set_reels_scenario_feedback(
        self,
        scenario_id: uuid.UUID,
        *,
        status: str,
        user_id: int,
        reach: Optional[int] = None,
    ) -> bool:
        status = (status or "").strip().lower()
        if status not in ("approved", "rejected", "published", "draft"):
            return False
        try:
            async with self.get_connection() as conn:
                if reach is not None:
                    await conn.execute(
                        """
                        UPDATE reels_scenarios
                        SET status = $2,
                            feedback_user_id = $3,
                            reach = $4,
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        scenario_id,
                        status,
                        int(user_id),
                        int(reach),
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE reels_scenarios
                        SET status = $2,
                            feedback_user_id = $3,
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        scenario_id,
                        status,
                        int(user_id),
                    )
            return True
        except Exception as e:
            logger.error("set_reels_scenario_feedback: %s", e)
            return False

    async def get_reels_scenario(
        self, scenario_id: uuid.UUID
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM reels_scenarios WHERE id = $1",
                    scenario_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_reels_scenario: %s", e)
            return None

    async def list_top_reels_few_shot(self, limit: int = 8) -> List[Dict[str, Any]]:
        """Лучшие сценарии для few-shot: published/approved с охватами."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT idea_title, scenario_text, reach, status
                    FROM reels_scenarios
                    WHERE status IN ('approved', 'published')
                      AND length(scenario_text) > 80
                    ORDER BY
                        CASE WHEN status = 'published' THEN 0 ELSE 1 END,
                        reach DESC NULLS LAST,
                        updated_at DESC
                    LIMIT $1
                    """,
                    max(1, min(20, int(limit))),
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_top_reels_few_shot: %s", e)
            return []
