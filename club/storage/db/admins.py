"""
Mixin: пользователи с доступом к админ-функциям в основном боте (`admins`).
"""

import logging

logger = logging.getLogger(__name__)


class AdminsMixin:

    async def is_telegram_admin_id(self, telegram_user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT 1 FROM admins WHERE telegram_user_id = $1
                    """,
                    telegram_user_id,
                )
                return row is not None
        except Exception as e:
            logger.error("❌ is_telegram_admin_id failed: %s", e)
            return False

    async def add_telegram_admin_id(self, telegram_user_id: int, note: str = "") -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO admins (telegram_user_id, note)
                    VALUES ($1, NULLIF($2, ''))
                    ON CONFLICT (telegram_user_id) DO UPDATE
                    SET note = COALESCE(NULLIF(EXCLUDED.note, ''), admins.note)
                    """,
                    telegram_user_id,
                    note or "",
                )
                return True
        except Exception as e:
            logger.error("❌ add_telegram_admin_id failed: %s", e)
            return False

    async def remove_telegram_admin_id(self, telegram_user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "DELETE FROM admins WHERE telegram_user_id = $1",
                    telegram_user_id,
                )
                return True
        except Exception as e:
            logger.error("❌ remove_telegram_admin_id failed: %s", e)
            return False

    async def list_telegram_admin_ids(self) -> list[dict]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT telegram_user_id, created_at, note
                    FROM admins
                    ORDER BY created_at ASC
                    """
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("❌ list_telegram_admin_ids failed: %s", e)
            return []
