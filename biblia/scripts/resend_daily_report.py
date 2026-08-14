#!/usr/bin/env python3
"""Пересобрать и отправить ежедневный отчёт Biblia (после reconcile оплат)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
logger = logging.getLogger("resend_daily_report")


async def main(env_file: str | None) -> None:
    if env_file:
        load_dotenv(env_file, override=True)
    else:
        load_dotenv(override=True)

    from bot_app import BotApplication

    app = BotApplication()
    await app.initialize()
    try:
        if app.payment_checker:
            await app.payment_checker.stop()
        feat = app.feature_manager.get("daily_admin_report")
        ok = await feat.send_report()
        logger.info("biblia report sent ok=%s", ok)
    finally:
        await app.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--env-file", default=None)
    args = p.parse_args()
    asyncio.run(main(args.env_file))
