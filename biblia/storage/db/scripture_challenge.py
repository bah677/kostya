"""Mixin: челлендж чтения Писания."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def parse_intake_transcript(raw: Any) -> List[Dict[str, str]]:
    """Нормализует intake_transcript из БД (list / JSON-строка / None)."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [m for m in raw if isinstance(m, dict)]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [m for m in parsed if isinstance(m, dict)]
    return []


class ScriptureChallengeMixin:

    async def create_scripture_challenge(self, user_id: int) -> Optional[int]:
        try:
            async with self.get_connection() as conn:
                row_id = await conn.fetchval(
                    """
                    INSERT INTO scripture_challenges (user_id, status, updated_at)
                    VALUES ($1, 'intake', NOW())
                    RETURNING id
                    """,
                    user_id,
                )
                return int(row_id) if row_id else None
        except Exception as e:
            logger.error("create_scripture_challenge uid=%s: %s", user_id, e)
            return None

    async def get_scripture_challenge(self, challenge_id: int) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM scripture_challenges WHERE id = $1",
                    challenge_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_scripture_challenge id=%s: %s", challenge_id, e)
            return None

    async def get_user_active_scripture_challenge(
        self, user_id: int
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM scripture_challenges
                     WHERE user_id = $1
                       AND status IN ('intake', 'planning', 'active')
                     ORDER BY created_at DESC
                     LIMIT 1
                    """,
                    user_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_user_active_scripture_challenge uid=%s: %s", user_id, e)
            return None

    async def update_scripture_challenge(
        self, challenge_id: int, **fields: Any
    ) -> bool:
        if not fields:
            return False
        allowed = {
            "status",
            "user_request_summary",
            "intake_transcript",
            "duration_days",
            "delivery_hour",
            "delivery_minute",
            "delivery_tz",
            "current_day",
            "plan_version",
            "started_at",
            "completed_at",
            "last_daily_sent_at",
            "last_weekly_review_at",
            "next_delivery_at",
            "next_weekly_review_at",
        }
        sets = []
        params: List[Any] = []
        idx = 1
        for key, val in fields.items():
            if key not in allowed:
                continue
            if key == "intake_transcript":
                if isinstance(val, str):
                    val = parse_intake_transcript(val)
                val = json.dumps(val, ensure_ascii=False)
                sets.append(f"{key} = ${idx}::jsonb")
            else:
                sets.append(f"{key} = ${idx}")
            params.append(val)
            idx += 1
        if not sets:
            return False
        sets.append("updated_at = NOW()")
        params.append(challenge_id)
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    f"UPDATE scripture_challenges SET {', '.join(sets)} WHERE id = ${idx}",
                    *params,
                )
                return True
        except Exception as e:
            logger.error("update_scripture_challenge id=%s: %s", challenge_id, e)
            return False

    async def append_intake_message(
        self, challenge_id: int, role: str, content: str
    ) -> bool:
        ch = await self.get_scripture_challenge(challenge_id)
        if not ch:
            return False
        transcript = parse_intake_transcript(ch.get("intake_transcript"))
        transcript.append({"role": role, "content": content})
        return await self.update_scripture_challenge(
            challenge_id, intake_transcript=transcript
        )

    async def replace_plan_items(
        self, challenge_id: int, items: List[Dict[str, Any]]
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "DELETE FROM scripture_challenge_plan_items WHERE challenge_id = $1",
                        challenge_id,
                    )
                    for it in items:
                        await conn.execute(
                            """
                            INSERT INTO scripture_challenge_plan_items (
                                challenge_id, day_number, sort_order,
                                reference, passage_text, theme_note, status
                            )
                            VALUES ($1, $2, $3, $4, $5, $6, 'pending')
                            """,
                            challenge_id,
                            int(it["day_number"]),
                            int(it.get("sort_order", it["day_number"])),
                            str(it["reference"]),
                            str(it["passage_text"]),
                            it.get("theme_note"),
                        )
                return True
        except Exception as e:
            logger.error("replace_plan_items challenge=%s: %s", challenge_id, e)
            return False

    async def get_plan_items(self, challenge_id: int) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM scripture_challenge_plan_items
                     WHERE challenge_id = $1
                     ORDER BY day_number ASC
                    """,
                    challenge_id,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("get_plan_items challenge=%s: %s", challenge_id, e)
            return []

    async def get_plan_item_for_day(
        self, challenge_id: int, day_number: int
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM scripture_challenge_plan_items
                     WHERE challenge_id = $1 AND day_number = $2
                    """,
                    challenge_id,
                    day_number,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_plan_item_for_day: %s", e)
            return None

    async def mark_plan_item_sent(self, item_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE scripture_challenge_plan_items
                       SET status = 'sent', sent_at = NOW()
                     WHERE id = $1
                    """,
                    item_id,
                )
                return True
        except Exception as e:
            logger.error("mark_plan_item_sent id=%s: %s", item_id, e)
            return False

    async def add_challenge_message(
        self, challenge_id: int, role: str, content: str
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO scripture_challenge_messages (challenge_id, role, content)
                    VALUES ($1, $2, $3)
                    """,
                    challenge_id,
                    role,
                    content,
                )
                return True
        except Exception as e:
            logger.error("add_challenge_message: %s", e)
            return False

    async def get_challenge_messages(
        self, challenge_id: int, *, limit: int = 30
    ) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT role, content, created_at
                      FROM scripture_challenge_messages
                     WHERE challenge_id = $1
                     ORDER BY created_at DESC
                     LIMIT $2
                    """,
                    challenge_id,
                    limit,
                )
                return [dict(r) for r in reversed(rows)]
        except Exception as e:
            logger.error("get_challenge_messages: %s", e)
            return []

    async def list_challenges_due_delivery(
        self, before: datetime
    ) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM scripture_challenges
                     WHERE status = 'active'
                       AND next_delivery_at IS NOT NULL
                       AND next_delivery_at <= $1
                    """,
                    before,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_challenges_due_delivery: %s", e)
            return []

    async def list_challenges_due_weekly_review(
        self, before: datetime
    ) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM scripture_challenges
                     WHERE status = 'active'
                       AND next_weekly_review_at IS NOT NULL
                       AND next_weekly_review_at <= $1
                    """,
                    before,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_challenges_due_weekly_review: %s", e)
            return []

    async def cancel_scripture_challenge(self, challenge_id: int) -> bool:
        return await self.update_scripture_challenge(challenge_id, status="cancelled")

    async def list_users_in_scripture_challenge(self) -> List[int]:
        """user_id с незавершённым челленджем (intake / planning / active)."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT sc.user_id
                      FROM scripture_challenges sc
                      JOIN users u ON u.user_id = sc.user_id
                     WHERE sc.status IN ('intake', 'planning', 'active')
                       AND u.is_active = TRUE
                     ORDER BY sc.user_id ASC
                    """
                )
                return [int(r["user_id"]) for r in rows]
        except Exception as e:
            logger.error("list_users_in_scripture_challenge: %s", e)
            return []

    async def patch_plan_items(
        self, challenge_id: int, items: List[Dict[str, Any]]
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                async with conn.transaction():
                    for it in items:
                        await conn.execute(
                            """
                            UPDATE scripture_challenge_plan_items
                               SET reference = $3,
                                   passage_text = $4,
                                   theme_note = $5,
                                   status = 'pending'
                             WHERE challenge_id = $1 AND day_number = $2
                            """,
                            challenge_id,
                            int(it["day_number"]),
                            str(it["reference"]),
                            str(it["passage_text"]),
                            it.get("theme_note"),
                        )
                ch = await self.get_scripture_challenge(challenge_id)
                if ch:
                    await self.update_scripture_challenge(
                        challenge_id, plan_version=int(ch.get("plan_version") or 1) + 1
                    )
                return True
        except Exception as e:
            logger.error("patch_plan_items challenge=%s: %s", challenge_id, e)
            return False

    @staticmethod
    def compute_next_delivery_at(
        *,
        hour: int,
        minute: int,
        tz_name: str = "Europe/Moscow",
        after: Optional[datetime] = None,
    ) -> datetime:
        tz = ZoneInfo(tz_name)
        base = after.astimezone(tz) if after else datetime.now(tz)
        candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= base:
            candidate += timedelta(days=1)
        return candidate
