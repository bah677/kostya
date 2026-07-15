"""
Базовый слой Database: пул подключений к PostgreSQL.
Все mixin'ы используют self.get_connection() из этого класса.
"""

import logging
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg
from storage.log_util import format_exc

logger = logging.getLogger(__name__)


class DatabaseBase:
    """Управление пулом подключений к PostgreSQL."""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool: Optional[asyncpg.Pool] = None
        self._reconnect_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Создаёт пул подключений к базе данных."""
        if self.pool is not None:
            return
        async with self._reconnect_lock:
            if self.pool is not None:
                return
            await self._create_pool()

    async def close(self) -> None:
        """Закрывает пул подключений."""
        async with self._reconnect_lock:
            old = self.pool
            self.pool = None
            if old:
                try:
                    await old.close()
                except Exception:
                    pass
                logger.info("✅ PostgreSQL connection pool closed")

    async def _create_pool(self) -> None:
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=5,
                max_size=20,
                command_timeout=60,
                max_inactive_connection_lifetime=300,
            )
            logger.info("✅ PostgreSQL connection pool created successfully")
        except Exception as e:
            self.pool = None
            logger.error(f"❌ Failed to connect to PostgreSQL: {e}")
            raise

    @asynccontextmanager
    async def get_connection(self):
        """Контекстный менеджер для получения подключения из пула."""
        last_exc: Optional[BaseException] = None
        for attempt in range(2):
            if not self.pool:
                await self.connect()
            try:
                async with self.pool.acquire() as connection:
                    yield connection
                return
            except Exception as e:
                last_exc = e
                if attempt == 0 and self._is_connection_lost_error(e):
                    logger.warning(
                        "⚠️ DB connection lost, recycling pool: %s",
                        format_exc(e),
                    )
                    await self._reconnect_pool()
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Database not connected")

    @staticmethod
    def _is_connection_lost_error(exc: BaseException) -> bool:
        low = format_exc(exc).lower()
        return (
            "connection_lost" in low
            or "connection lost" in low
            or "connection is closed" in low
            or "pool is closed" in low
            or "cannot switch to state" in low
            or "unexpected connection_lost() call" in low
            or isinstance(exc, (ConnectionError, asyncpg.PostgresConnectionError))
        )

    async def _reconnect_pool(self) -> None:
        """Пересоздаёт пул без окна, когда self.pool = None (фоновые задачи не падают)."""
        async with self._reconnect_lock:
            old = self.pool
            try:
                await self._create_pool()
            except Exception:
                if old is not None and self.pool is None:
                    self.pool = old
                raise
            if old is not None and old is not self.pool:
                try:
                    await old.close()
                except Exception:
                    pass
            logger.info("✅ PostgreSQL pool recycled")
