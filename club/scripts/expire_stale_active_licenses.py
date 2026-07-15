#!/usr/bin/env python3
"""Переводит в expired active-лицензии после CLUB_GROUP_EXPIRED_LICENSE_GRACE_DAYS."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("expire_stale_active_licenses")


def _load_env(env_file: str | None) -> None:
    if env_file:
        load_dotenv(env_file, override=True)
    else:
        load_dotenv(override=True)


async def run(*, dry_run: bool, env_file: str | None) -> None:
    _load_env(env_file)
    from config import config
    from storage.user_storage import UserStorage

    logger.info("База данных: %s", config.DB_NAME)
    storage = UserStorage(config.database_url)
    await storage.initialize()
    grace = max(0, int(config.CLUB_GROUP_EXPIRED_LICENSE_GRACE_DAYS))
    logger.info("Отсрочка (grace_days): %s", grace)
    try:
        async with storage.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT l.user_id, u.first_name, u.username, l.expires_at, l.license_type
                FROM license l
                LEFT JOIN users u ON u.user_id = l.user_id
                WHERE l.status = 'active'
                  AND l.expires_at <= NOW() - make_interval(days => $1::int)
                ORDER BY l.user_id
                """,
                grace,
            )
        logger.info("Найдено устаревших active-лицензий: %s", len(rows))
        for row in rows:
            un = f"@{row['username']}" if row.get("username") else "—"
            logger.info(
                "  uid=%s %s %s expires=%s type=%s",
                row["user_id"],
                row.get("first_name") or "",
                un,
                row["expires_at"],
                row.get("license_type"),
            )
        if dry_run:
            logger.info("dry-run: изменения не применялись")
            return
        fixed = await storage.expire_stale_active_licenses(
            grace_days=config.CLUB_GROUP_EXPIRED_LICENSE_GRACE_DAYS,
        )
        logger.info("Переведено в expired: %s", fixed)
    finally:
        await storage.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать список, без UPDATE",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Путь к .env (например /home/appuser/club/.env для prod)",
    )
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run, env_file=args.env_file))


if __name__ == "__main__":
    main()
