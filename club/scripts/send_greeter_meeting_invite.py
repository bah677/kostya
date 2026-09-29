#!/usr/bin/env python3
"""Первая рассылка: закрытая встреча встречающих 30.09 13:00.

Запуск из dev с prod .env (после деплоя, чтобы кнопки работали на проде):

  cd /home/appuser/dev/kostya/club
  ./venv/bin/python scripts/send_greeter_meeting_invite.py --dry-run
  ./venv/bin/python scripts/send_greeter_meeting_invite.py --send
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot
from dotenv import load_dotenv

from bot.services import greeter_meeting_service as gm
from bot.texts import ru_greeter_meeting as txt
from config import load_config
from storage.user_storage import UserStorage

logger = logging.getLogger("send_greeter_meeting_invite")
DEFAULT_PROD_ENV = "/home/appuser/club/.env"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=DEFAULT_PROD_ENV)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--pause", type=float, default=gm.PAUSE_SEC)
    args = parser.parse_args()
    if not args.dry_run and not args.send:
        parser.error("укажите --dry-run или --send")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    load_dotenv(args.env, override=True)
    cfg = load_config()

    storage = UserStorage(cfg.database_url)
    await storage.connect()
    try:
        recipients = await gm.collect_meeting_recipients(storage)
        logger.info("recipients=%s", len(recipients))
        for uid in recipients:
            logger.info("  uid=%s", uid)

        print("--- INVITE ---")
        print(txt.INVITE_HTML)
        print("--- BUTTONS ---")
        print(txt.BTN_COMING)
        print(txt.BTN_CANT)

        if args.dry_run:
            logger.info("dry-run only")
            return

        bot = Bot(token=cfg.MIRON_BOT_TOKEN)
        try:
            stats = await gm.blast_invite(
                bot, storage, recipients=recipients, pause_sec=args.pause
            )
            logger.info("done %s", stats)
            print(stats)
        finally:
            await bot.session.close()
    finally:
        await storage.close()


if __name__ == "__main__":
    asyncio.run(main())
