"""
Mixin: маппинг user_id → forum topic_id для персональных топиков диалогов
в специальной Telegram-супергруппе (DIALOG_FORUM_GROUP_ID).
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class DialogTopicsMixin:

    async def get_dialog_topic_id(self, user_id: int) -> Optional[int]:
        """Возвращает topic_id для пользователя или None, если маппинга нет."""
        try:
            async with self.get_connection() as conn:
                return await conn.fetchval(
                    "SELECT topic_id FROM dialog_topics WHERE user_id = $1",
                    user_id,
                )
        except Exception as e:
            logger.error("get_dialog_topic_id user=%s: %s", user_id, e)
            return None

    async def get_user_id_by_dialog_topic(self, topic_id: int) -> Optional[int]:
        """Обратный lookup: topic_id → user_id (для ответов админа в форум-топике)."""
        try:
            async with self.get_connection() as conn:
                return await conn.fetchval(
                    "SELECT user_id FROM dialog_topics WHERE topic_id = $1",
                    topic_id,
                )
        except Exception as e:
            logger.error("get_user_id_by_dialog_topic topic=%s: %s", topic_id, e)
            return None

    async def upsert_dialog_topic(self, user_id: int, topic_id: int) -> None:
        """Создаёт или перезаписывает маппинг user_id → topic_id."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO dialog_topics (user_id, topic_id)
                    VALUES ($1, $2)
                    ON CONFLICT (user_id) DO UPDATE
                        SET topic_id   = EXCLUDED.topic_id,
                            updated_at = NOW()
                    """,
                    user_id,
                    topic_id,
                )
        except Exception as e:
            logger.error("upsert_dialog_topic user=%s topic=%s: %s", user_id, topic_id, e)

    async def clear_dialog_topic(self, user_id: int) -> None:
        """Удаляет маппинг (битый/удалённый топик в Telegram)."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "DELETE FROM dialog_topics WHERE user_id = $1",
                    user_id,
                )
        except Exception as e:
            logger.error("clear_dialog_topic user=%s: %s", user_id, e)
