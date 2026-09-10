"""Состояние первой недели новичка в группе (club_first_week)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")


class ClubFirstWeekMixin:
    async def start_club_first_week(
        self,
        user_id: int,
        *,
        origin: str = "payment",
        started_at: Optional[datetime] = None,
        deadline_at: Optional[datetime] = None,
    ) -> bool:
        """Первый цикл первой недели. Повторный вход не перезапускает."""
        started_at = started_at or datetime.now(MSK)
        origin = (origin or "payment").strip()[:32] or "payment"
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO club_first_week
                        (user_id, origin, started_at, deadline_at, step, msgs_group)
                    VALUES ($1, $2, $3, $4, 1, 0)
                    ON CONFLICT (user_id) DO NOTHING
                    RETURNING user_id
                    """,
                    user_id,
                    origin,
                    started_at,
                    deadline_at,
                )
                return bool(row)
        except Exception as e:
            logger.error("start_club_first_week uid=%s: %s", user_id, e, exc_info=True)
            return False

    async def get_club_first_week(self, user_id: int) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM club_first_week WHERE user_id = $1", user_id
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_club_first_week: %s", e)
            return None

    async def list_open_first_week(self, *, limit: int = 200) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT *
                    FROM club_first_week
                    WHERE ended_at IS NULL
                    ORDER BY started_at ASC
                    LIMIT $1
                    """,
                    limit,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_open_first_week: %s", e)
            return []

    async def bump_first_week_group_msg(self, user_id: int) -> Optional[Dict[str, Any]]:
        """+1 к msgs_group; при >=3 ставит done_at. Возвращает обновлённую строку."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE club_first_week
                    SET msgs_group = msgs_group + 1,
                        done_at = CASE
                            WHEN msgs_group + 1 >= 3 AND done_at IS NULL
                                THEN NOW()
                            ELSE done_at
                        END,
                        updated_at = NOW()
                    WHERE user_id = $1
                      AND ended_at IS NULL
                    RETURNING *
                    """,
                    user_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("bump_first_week_group_msg: %s", e)
            return None

    async def mark_first_week_step(
        self, user_id: int, step: int, *, when: Optional[datetime] = None
    ) -> bool:
        when = when or datetime.now(MSK)
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE club_first_week
                    SET step = GREATEST(step, $2),
                        last_step_at = $3,
                        updated_at = NOW()
                    WHERE user_id = $1
                    """,
                    user_id,
                    int(step),
                    when,
                )
                return True
        except Exception as e:
            logger.error("mark_first_week_step: %s", e)
            return False

    async def end_club_first_week(
        self, user_id: int, *, reason: str = "timeout"
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE club_first_week
                    SET ended_at = NOW(), updated_at = NOW()
                    WHERE user_id = $1 AND ended_at IS NULL
                    """,
                    user_id,
                )
                return True
        except Exception as e:
            logger.error("end_club_first_week uid=%s: %s", user_id, e)
            return False

    async def assign_step4_variant(self, user_id: int) -> str:
        """Сплит шага 4: половина air / половина holdout."""
        variant = "air" if (int(user_id) % 2 == 0) else "holdout"
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE club_first_week
                    SET step4_variant = COALESCE(step4_variant, $2),
                        updated_at = NOW()
                    WHERE user_id = $1
                    """,
                    user_id,
                    variant,
                )
        except Exception as e:
            logger.error("assign_step4_variant: %s", e)
        return variant

    async def close_expired_first_weeks(self) -> int:
        """ended_at если прошло 7 дней от started_at."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    UPDATE club_first_week
                    SET ended_at = NOW(), updated_at = NOW()
                    WHERE ended_at IS NULL
                      AND started_at <= NOW() - INTERVAL '7 days'
                    RETURNING user_id
                    """
                )
                return len(rows)
        except Exception as e:
            logger.error("close_expired_first_weeks: %s", e)
            return 0

    async def close_first_weeks_without_access(self) -> int:
        """Стоп, если нет действующей лицензии."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    UPDATE club_first_week cfw
                    SET ended_at = NOW(), updated_at = NOW()
                    WHERE cfw.ended_at IS NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM license l
                          WHERE l.user_id = cfw.user_id
                            AND l.status = 'active'
                            AND l.expires_at > NOW()
                      )
                    RETURNING user_id
                    """
                )
                return len(rows)
        except Exception as e:
            logger.error("close_first_weeks_without_access: %s", e)
            return 0
