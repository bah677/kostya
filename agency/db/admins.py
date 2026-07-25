"""CRUD таблицы admins (agency DB)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import asyncpg

logger = logging.getLogger(__name__)


async def is_telegram_admin_id(pool: asyncpg.Pool, telegram_user_id: int) -> bool:
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchval(
                "SELECT 1 FROM admins WHERE telegram_user_id = $1",
                int(telegram_user_id),
            )
            return row is not None
    except Exception as e:
        logger.error("is_telegram_admin_id: %s", e)
        return False


async def add_telegram_admin_id(
    pool: asyncpg.Pool,
    telegram_user_id: int,
    *,
    note: str = "",
    created_by: Optional[int] = None,
) -> bool:
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO admins (telegram_user_id, note, created_by)
                VALUES ($1, NULLIF($2, ''), $3)
                ON CONFLICT (telegram_user_id) DO UPDATE
                SET note = COALESCE(NULLIF(EXCLUDED.note, ''), admins.note)
                """,
                int(telegram_user_id),
                note or "",
                created_by,
            )
            return True
    except Exception as e:
        logger.error("add_telegram_admin_id: %s", e)
        return False


async def remove_telegram_admin_id(pool: asyncpg.Pool, telegram_user_id: int) -> bool:
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM admins WHERE telegram_user_id = $1",
                int(telegram_user_id),
            )
            return True
    except Exception as e:
        logger.error("remove_telegram_admin_id: %s", e)
        return False


async def list_telegram_admin_ids(pool: asyncpg.Pool) -> List[Dict[str, Any]]:
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT telegram_user_id, note, created_at, created_by
                FROM admins
                ORDER BY created_at ASC
                """
            )
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("list_telegram_admin_ids: %s", e)
        return []
