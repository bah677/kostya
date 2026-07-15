"""
Mixin: ответы администраторов клиенту (`admin_responses`).
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AdminResponsesMixin:

    async def create_admin_response(
        self,
        user_id: int,
        message_text: str,
        admin_id: Optional[int] = None,
    ) -> Optional[int]:
        """Кладёт в очередь ответ админа для клиента."""
        try:
            async with self.get_connection() as conn:
                response_id = await conn.fetchval(
                    """
                    INSERT INTO admin_responses (user_id, message_text, admin_id, status)
                    VALUES ($1, $2, $3, 'pending')
                    RETURNING id
                    """,
                    user_id, message_text, admin_id,
                )
                logger.info(
                    f"✅ Admin response created: id={response_id}, user_id={user_id}"
                )
                return response_id
        except Exception as e:
            logger.error(f"❌ Failed to create admin response: {e}")
            return None

    async def get_pending_admin_responses(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Список pending-ответов админов для отправки."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, user_id, message_text, admin_id, created_at
                    FROM admin_responses
                    WHERE status = 'pending'
                    ORDER BY created_at ASC
                    LIMIT $1
                    """,
                    limit,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get pending admin responses: {e}")
            return []

    async def update_admin_response_status(
        self,
        response_id: int,
        status: str,
        error: Optional[str] = None,
    ) -> bool:
        """Обновляет статус (sent/failed) ответа админа."""
        try:
            async with self.get_connection() as conn:
                if status == "sent":
                    await conn.execute(
                        """
                        UPDATE admin_responses
                        SET status = $1, sent_at = NOW(), updated_at = NOW()
                        WHERE id = $2
                        """,
                        status, response_id,
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE admin_responses
                        SET status = $1, error = $2, updated_at = NOW()
                        WHERE id = $3
                        """,
                        status, error, response_id,
                    )
                logger.info(f"✅ Admin response {response_id} status updated to {status}")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update admin response status: {e}")
            return False
