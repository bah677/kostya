"""Agency entrypoint: Telegram bot + nightly scheduler + CLI."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

# allow `python main.py` from agency/
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.handlers_admin import register_with_ctx
from config import config, load_config
from db.pool import Pools
from runtime.orchestrator import run_nightly

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("agency")


async def apply_migrations(pools: Pools) -> None:
    assert pools.agency
    mig_dir = ROOT / "migrations"
    files = sorted(mig_dir.glob("*.sql"))
    async with pools.agency.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        for path in files:
            name = path.name
            exists = await conn.fetchval(
                "SELECT 1 FROM schema_migrations WHERE filename = $1", name
            )
            if exists:
                continue
            sql = path.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES ($1)", name
                )
            logger.info("migration applied: %s", name)


async def run_bot() -> None:
    cfg = load_config()
    if not cfg.AGENCY_BOT_TOKEN:
        raise SystemExit("AGENCY_BOT_TOKEN is empty")

    pools = Pools()
    await pools.open(cfg)
    await apply_migrations(pools)

    bot = Bot(token=cfg.AGENCY_BOT_TOKEN)
    dp = Dispatcher()
    register_with_ctx(dp, cfg, pools, bot)

    scheduler = AsyncIOScheduler(timezone=cfg.TIMEZONE)

    async def _tick():
        try:
            await run_nightly(cfg=cfg, pools=pools, bot=bot, skip_llm=False)
        except Exception:
            logger.exception("scheduled nightly failed")

    scheduler.add_job(
        _tick,
        CronTrigger(
            hour=cfg.CRON_HOUR_MSK,
            minute=cfg.CRON_MINUTE_MSK,
            timezone=cfg.TIMEZONE,
        ),
        id="agency_nightly",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        "agency bot started; cron %02d:%02d %s",
        cfg.CRON_HOUR_MSK,
        cfg.CRON_MINUTE_MSK,
        cfg.TIMEZONE,
    )
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await pools.close()
        await bot.session.close()


async def run_once(day: str | None, skip_llm: bool) -> None:
    cfg = load_config()
    pools = Pools()
    await pools.open(cfg)
    await apply_migrations(pools)
    bot = None
    if cfg.AGENCY_BOT_TOKEN:
        bot = Bot(token=cfg.AGENCY_BOT_TOKEN)
    try:
        d = date.fromisoformat(day) if day else None
        result = await run_nightly(
            cfg=cfg, pools=pools, bot=bot, day=d, skip_llm=skip_llm
        )
        print(result.get("brief") or result)
    finally:
        await pools.close()
        if bot:
            await bot.session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Kostya agency")
    parser.add_argument(
        "command",
        nargs="?",
        default="bot",
        choices=["bot", "run", "migrate"],
    )
    parser.add_argument("--day", help="YYYY-MM-DD (default: yesterday MSK)")
    parser.add_argument("--skip-llm", action="store_true")
    args = parser.parse_args()

    if args.command == "bot":
        asyncio.run(run_bot())
    elif args.command == "migrate":
        async def _m():
            cfg = load_config()
            pools = Pools()
            await pools.open(cfg)
            await apply_migrations(pools)
            await pools.close()

        asyncio.run(_m())
    else:
        asyncio.run(run_once(args.day, args.skip_llm))


if __name__ == "__main__":
    main()
