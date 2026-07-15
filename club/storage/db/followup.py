"""
Mixin: дожим (поля followup_step* в `users`).
Сами таблицы followup_states/followup_messages/followup_log использует FollowupStorage напрямую.
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class FollowupMixin:

    async def get_users_for_followup(self, step: int, inactive_minutes: int) -> List[Dict[str, Any]]:
        """Пользователи на заданном шаге дожима, неактивные >= inactive_minutes."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT user_id, followup_step, followup_started_at, followup_last_action,
                           first_name, last_name, username, timezone_offset
                    FROM users
                    WHERE followup_step = $1
                      AND last_activity <= NOW() - INTERVAL '1 minute' * $2
                      AND is_active = TRUE
                    ORDER BY followup_last_action NULLS FIRST, last_activity
                    """,
                    step, inactive_minutes,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get users for followup step {step}: {e}")
            return []

    async def update_followup_step(self, user_id: int, step: int) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE users
                    SET followup_step = $1, followup_last_action = NOW()
                    WHERE user_id = $2
                    """,
                    step, user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update followup step for user_id={user_id}: {e}")
            return False

    async def start_followup(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE users
                    SET followup_step = 1,
                        followup_started_at = NOW(),
                        followup_last_action = NOW()
                    WHERE user_id = $1 AND (followup_step IS NULL OR followup_step = 0)
                    """,
                    user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to start followup for user_id={user_id}: {e}")
            return False

    async def save_user_feedback(self, user_id: int, feedback_text: str) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE users
                    SET feedback_text = $1,
                        feedback_given_at = NOW(),
                        followup_step = 2
                    WHERE user_id = $2
                    """,
                    feedback_text, user_id,
                )
                logger.info(f"✅ Feedback saved for user_id={user_id}")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to save feedback for user_id={user_id}: {e}")
            return False

    async def get_users_for_followup_step_time_aware(
        self,
        step: int,
        inactive_minutes: int,
        night_hours: tuple = (23, 6),
    ) -> List[Dict[str, Any]]:
        """Дожим с учётом локального времени (night_hours = промежуток без отправки)."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    WITH user_local_time AS (
                        SELECT
                            user_id,
                            followup_step,
                            followup_started_at,
                            followup_last_action,
                            first_name,
                            last_name,
                            username,
                            timezone_offset,
                            last_activity,
                            (NOW() + (timezone_offset || ' minutes')::interval) as local_now
                        FROM users
                        WHERE followup_step = $1
                          AND last_activity <= NOW() - INTERVAL '1 minute' * $2
                          AND is_active = TRUE
                    )
                    SELECT * FROM user_local_time
                    WHERE EXTRACT(HOUR FROM local_now) NOT BETWEEN $4 AND $3
                    ORDER BY followup_last_action NULLS FIRST, last_activity
                    """,
                    step, inactive_minutes, night_hours[0], night_hours[1],
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get time-aware users for followup: {e}")
            return []
