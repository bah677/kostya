"""Mixin: инциденты с тех. сбоями бота (рассылка «напишите снова»)."""

from __future__ import annotations

import logging
from typing import List, Sequence

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prayer_tech_incidents (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users (user_id),
    kind        VARCHAR(64) NOT NULL,
    detail      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_prayer_tech_incidents_created
    ON prayer_tech_incidents (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_prayer_tech_incidents_user_created
    ON prayer_tech_incidents (user_id, created_at DESC);
"""

# Исходящие сообщения бота с типичными текстами сбоя (личка).
_OUTAGE_MESSAGE_PATTERNS = (
    "%Что-то пошло не так%",
    "%Не удалось получить ответ%",
    "%Сервис временно недоступен%",
    "%Произошла ошибка%",
    "%Не удалось подготовить молитву%",
    "%Не удалось составить молитву%",
    "%В этом чате голосовые недоступны%",
    "%не удалось отправить%",
    "%не удалось обработать%",
)


class PrayerTechIncidentsMixin:
    async def ensure_prayer_tech_incidents_schema(self) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(_SCHEMA)
        except Exception as e:
            logger.warning("ensure_prayer_tech_incidents_schema: %s", e)

    async def log_prayer_tech_incident(
        self,
        user_id: int,
        kind: str,
        *,
        detail: str = "",
    ) -> None:
        uid = int(user_id or 0)
        if uid <= 0:
            return
        kind_s = (kind or "unknown").strip()[:64] or "unknown"
        try:
            await self.ensure_prayer_tech_incidents_schema()
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO prayer_tech_incidents (user_id, kind, detail)
                    VALUES ($1, $2, $3)
                    """,
                    uid,
                    kind_s,
                    (detail or "")[:2000] or None,
                )
        except Exception as e:
            logger.warning(
                "log_prayer_tech_incident uid=%s kind=%s: %s", uid, kind_s, e
            )

    async def log_bot_tech_incident(
        self,
        user_id: int,
        kind: str,
        *,
        detail: str = "",
    ) -> None:
        await self.log_prayer_tech_incident(user_id, kind, detail=detail)

    async def list_prayer_tech_affected_user_ids(
        self,
        *,
        hours: int = 24,
        exclude_user_ids: Sequence[int] = (),
    ) -> List[int]:
        return await self.list_bot_tech_affected_user_ids(
            hours=hours, exclude_user_ids=exclude_user_ids
        )

    async def list_bot_tech_affected_user_ids(
        self,
        *,
        hours: int = 24,
        exclude_user_ids: Sequence[int] = (),
    ) -> List[int]:
        """
        Кому слать «напишите снова»: инциденты + эвристики по messages/users за N часов.
        """
        hrs = max(1, min(168, int(hours)))
        excl = [int(x) for x in exclude_user_ids if int(x) > 0]
        msg_cond = " OR ".join(
            f"m.content ILIKE ${i + 3}" for i in range(len(_OUTAGE_MESSAGE_PATTERNS))
        )
        params: list = [hrs, excl, *_OUTAGE_MESSAGE_PATTERNS]
        try:
            await self.ensure_prayer_tech_incidents_schema()
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT DISTINCT uid AS user_id
                    FROM (
                        SELECT user_id AS uid
                          FROM prayer_tech_incidents
                         WHERE created_at >= NOW() - ($1::int * INTERVAL '1 hour')
                        UNION
                        SELECT m.user_id AS uid
                          FROM messages m
                         WHERE m.sender_type = 'bot'
                           AND m.user_id = m.chat_id
                           AND m.created_at >= NOW() - ($1::int * INTERVAL '1 hour')
                           AND ({msg_cond})
                        UNION
                        SELECT u.user_id AS uid
                          FROM users u
                         WHERE COALESCE(u.prayer_voice_unlock_pending, FALSE)
                           AND u.prayer_last_text_at >= NOW() - ($1::int * INTERVAL '1 hour')
                    ) t
                    WHERE uid > 0
                      AND (CARDINALITY($2::bigint[]) = 0 OR NOT (uid = ANY($2::bigint[])))
                    ORDER BY uid
                    """,
                    *params,
                )
            return [int(r["user_id"]) for r in rows if r.get("user_id")]
        except Exception as e:
            logger.error("list_bot_tech_affected_user_ids: %s", e)
            return []
