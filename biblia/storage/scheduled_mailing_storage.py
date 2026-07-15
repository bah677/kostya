"""Хранилище запланированных рассылок (расписания, логи, выборка пользователей)."""

import logging
import random
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ACTIVE_CHALLENGE_EXCLUDE_SQL = """
  AND NOT EXISTS (
    SELECT 1
      FROM scripture_challenges sc
     WHERE sc.user_id = users.user_id
       AND sc.status = 'active'
  )
"""


class ScheduledMailingStorage:
    """Расписания ``mailing_schedules`` и выборка аудитории; отправка делегирована кампаниям ``mailing_campaigns``."""

    def __init__(self, db):
        self.db = db

    async def get_all_mailing_schedules(self, active_only: bool = False) -> List[Dict[str, Any]]:
        try:
            async with self.db.get_connection() as conn:
                query = "SELECT * FROM mailing_schedules"
                if active_only:
                    query += " WHERE is_active = true"
                query += " ORDER BY id"
                rows = await conn.fetch(query)
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("❌ get_all_mailing_schedules: %s", e)
            return []

    async def get_mailing_schedule(self, schedule_id: int) -> Optional[Dict[str, Any]]:
        try:
            async with self.db.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM mailing_schedules WHERE id = $1", schedule_id
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("❌ get_mailing_schedule id=%s: %s", schedule_id, e)
            return None

    async def update_generated_text(self, schedule_id: int, text: str) -> bool:
        try:
            async with self.db.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE mailing_schedules
                       SET last_generated_text = $1,
                           last_generated_at = NOW(),
                           updated_at = NOW()
                     WHERE id = $2
                    """,
                    text,
                    schedule_id,
                )
                return True
        except Exception as e:
            logger.error("❌ update_generated_text sid=%s: %s", schedule_id, e)
            return False

    async def create_mailing_log(
        self,
        user_id: int,
        schedule_id: int,
        message_text: str,
        status: str = "pending",
    ) -> int:
        try:
            async with self.db.get_connection() as conn:
                return await conn.fetchval(
                    """
                    INSERT INTO mailing_logs (user_id, schedule_id, message_text, status)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id
                    """,
                    user_id,
                    schedule_id,
                    message_text,
                    status,
                )
        except Exception as e:
            logger.error("❌ create_mailing_log: %s", e)
            raise

    async def update_mailing_log(
        self,
        log_id: int,
        status: str,
        telegram_message_id: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        try:
            async with self.db.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE mailing_logs
                       SET status = $1,
                           telegram_message_id = $2,
                           error_message = $3,
                           sent_at = CASE
                             WHEN $1 IN ('sent', 'blocked', 'failed') THEN NOW()
                             ELSE sent_at
                           END
                     WHERE id = $4
                    """,
                    status,
                    telegram_message_id,
                    error_message,
                    log_id,
                )
                return True
        except Exception as e:
            logger.error("❌ update_mailing_log id=%s: %s", log_id, e)
            return False

    async def update_user_mailing_stats(self, user_id: int, *, sent: bool = True) -> bool:
        try:
            async with self.db.get_connection() as conn:
                if sent:
                    await conn.execute(
                        """
                        UPDATE users
                           SET last_mailing_at = NOW(),
                               mailing_count = mailing_count + 1
                         WHERE user_id = $1
                        """,
                        user_id,
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE users SET is_active = false WHERE user_id = $1
                        """,
                        user_id,
                    )
                return True
        except Exception as e:
            logger.error("❌ update_user_mailing_stats uid=%s: %s", user_id, e)
            return False

    async def get_random_users_for_blessing_mailing(self) -> List[Dict[str, Any]]:
        """Случайная аудитория для благословения (независимо от цитат)."""
        return await self._get_random_mailing_sample(kind="blessing")

    async def get_random_users_for_scripture_mailing(self) -> List[Dict[str, Any]]:
        """Случайная аудитория для цитаты из Писания (независимо от благословений)."""
        return await self._get_random_mailing_sample(kind="scripture")

    async def _get_random_mailing_sample(self, *, kind: str) -> List[Dict[str, Any]]:
        """
        Каждый день — случайное подмножество активных подписчиков.

        Если ``N`` — число пользователей с согласием, каждый день берём случайное ``k``.
        При равномерном отборе подмножества размера ``k``: для типичного пользователя
        ожидаемый интервал между попаданиями ≈ ``N/k`` календарных дней при большом ``N``.

        Целевые интервалы: благословение — в среднем раз в ``7–10`` дней, цитата — раз в ``2–3``.
        Конкретный ``k``: ``round(N / mean_spacing * jitter)`` с ``mean_spacing`` в этих диапазонах.
        """
        kind = (kind or "").strip().lower()
        if kind not in ("blessing", "scripture"):
            logger.error("❌ _get_random_mailing_sample: invalid kind=%s", kind)
            return []
        try:
            async with self.db.get_connection() as conn:
                stats = await conn.fetchrow(
                    f"""
                    SELECT
                        COUNT(*) AS total_active
                    FROM users
                    WHERE is_active = true
                      AND mailing_consent = true
                      {_ACTIVE_CHALLENGE_EXCLUDE_SQL}
                    """
                )
                if not stats or stats["total_active"] == 0:
                    return []

                total_active = int(stats["total_active"])
                if kind == "blessing":
                    mean_spacing = random.uniform(7.0, 10.0)
                else:
                    mean_spacing = random.uniform(2.0, 3.0)
                jitter = random.uniform(0.93, 1.07)
                ideal_k = total_active / mean_spacing
                users_to_send = int(round(ideal_k * jitter))
                users_to_send = max(1, min(total_active, users_to_send))

                logger.info(
                    "📊 mailing sample [%s]: N=%s mean_spacing_draw≈%.2f→k=%s (~N/k≈%.1f дн на пользователя)",
                    kind,
                    total_active,
                    mean_spacing,
                    users_to_send,
                    total_active / users_to_send,
                )

                rows = await conn.fetch(
                    f"""
                    SELECT
                        user_id,
                        first_name,
                        username,
                        last_mailing_at,
                        mailing_count
                    FROM users
                    WHERE is_active = true
                      AND mailing_consent = true
                      {_ACTIVE_CHALLENGE_EXCLUDE_SQL}
                    ORDER BY RANDOM()
                    LIMIT $1
                    """,
                    users_to_send,
                )
                return [dict(row) for row in rows]

        except Exception as e:
            logger.error("❌ _get_random_mailing_sample kind=%s: %s", kind, e)
            return []
