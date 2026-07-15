"""
Mixin: пользователи с доступом к админ-функциям в основном боте (`admins`).

На проде Biblia таблица могла быть создана раньше с колонкой ``user_id``
(legacy), а не ``telegram_user_id`` (club-style). Поддерживаем обе схемы.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Кэш на экземпляре Database: 'telegram_user_id' | 'user_id'
_admins_id_column_cache: dict[int, str] = {}


class AdminsMixin:

    async def _admins_id_column(self, conn) -> str:
        key = id(conn._pool) if hasattr(conn, "_pool") else 0
        cached = _admins_id_column_cache.get(key)
        if cached:
            return cached
        col = await conn.fetchval(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'admins'
              AND column_name IN ('telegram_user_id', 'user_id')
            ORDER BY CASE column_name
                WHEN 'telegram_user_id' THEN 0
                ELSE 1
            END
            LIMIT 1
            """
        )
        if not col:
            col = "telegram_user_id"
        _admins_id_column_cache[key] = col
        return col

    async def _admins_has_is_active(self, conn) -> bool:
        val = await conn.fetchval(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'admins'
              AND column_name = 'is_active'
            """
        )
        return bool(val)

    async def is_telegram_admin_id(self, telegram_user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                id_col = await self._admins_id_column(conn)
                active = await self._admins_has_is_active(conn)
                active_clause = " AND COALESCE(is_active, TRUE)" if active else ""
                row = await conn.fetchrow(
                    f"""
                    SELECT 1 FROM admins
                    WHERE {id_col} = $1{active_clause}
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
                id_col = await self._admins_id_column(conn)
                cols = [id_col]
                vals = ["$1"]
                args: list = [telegram_user_id]
                idx = 2
                if id_col == "telegram_user_id":
                    has_user_id = await conn.fetchval(
                        """
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = 'admins'
                          AND column_name = 'user_id'
                        """
                    )
                    if has_user_id:
                        cols.append("user_id")
                        vals.append(f"${idx}")
                        args.append(telegram_user_id)
                        idx += 1
                if note:
                    cols.append("note")
                    vals.append(f"NULLIF(${idx}, '')")
                    args.append(note)
                    idx += 1
                sql = (
                    f"INSERT INTO admins ({', '.join(cols)}) "
                    f"VALUES ({', '.join(vals)}) "
                    f"ON CONFLICT ({id_col}) DO NOTHING"
                )
                await conn.execute(sql, *args)
                return True
        except Exception as e:
            logger.error("❌ add_telegram_admin_id failed: %s", e)
            return False

    async def remove_telegram_admin_id(self, telegram_user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                id_col = await self._admins_id_column(conn)
                await conn.execute(
                    f"DELETE FROM admins WHERE {id_col} = $1",
                    telegram_user_id,
                )
                return True
        except Exception as e:
            logger.error("❌ remove_telegram_admin_id failed: %s", e)
            return False

    async def list_telegram_admin_ids(self) -> list[dict]:
        try:
            async with self.get_connection() as conn:
                id_col = await self._admins_id_column(conn)
                rows = await conn.fetch(
                    f"""
                    SELECT {id_col} AS telegram_user_id,
                           created_at,
                           note
                    FROM admins
                    ORDER BY created_at ASC NULLS LAST
                    """
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("❌ list_telegram_admin_ids failed: %s", e)
            return []
