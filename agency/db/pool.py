"""Пулы asyncpg: agency (RW), biblia/club (RO)."""

from __future__ import annotations

import logging
from typing import Optional

import asyncpg

from config import Config, DbDsn

logger = logging.getLogger(__name__)


class Pools:
    def __init__(self) -> None:
        self.agency: Optional[asyncpg.Pool] = None
        self.biblia: Optional[asyncpg.Pool] = None
        self.club: Optional[asyncpg.Pool] = None

    async def open(self, cfg: Config) -> None:
        self.agency = await _create(cfg.AGENCY_DB, "agency", min_size=1, max_size=4)
        self.biblia = await _create(cfg.BIBLIA_DB, "biblia", min_size=1, max_size=3)
        self.club = await _create(cfg.CLUB_DB, "club", min_size=1, max_size=3)

    async def close(self) -> None:
        for name, pool in (
            ("agency", self.agency),
            ("biblia", self.biblia),
            ("club", self.club),
        ):
            if pool is not None:
                await pool.close()
                logger.info("pool closed: %s", name)
        self.agency = self.biblia = self.club = None


async def _create(
    dsn: DbDsn, label: str, *, min_size: int, max_size: int
) -> asyncpg.Pool:
    if not dsn.user or not dsn.name:
        raise RuntimeError(f"{label} DB credentials incomplete")
    pool = await asyncpg.create_pool(
        **dsn.as_asyncpg(),
        min_size=min_size,
        max_size=max_size,
        command_timeout=120,
    )
    logger.info("pool open: %s db=%s user=%s", label, dsn.name, dsn.user)
    return pool
