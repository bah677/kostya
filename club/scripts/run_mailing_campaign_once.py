#!/usr/bin/env python3
"""Разовая отправка одной mailing-кампании (тест)."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import load_dotenv

from config import load_config
from bot.features.mailing import MailingFeature
from storage.user_storage import UserStorage


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-id", type=int, required=True)
    parser.add_argument("--env-file", default="/home/appuser/club/.env")
    args = parser.parse_args()

    load_dotenv(args.env_file, override=True)
    cfg = load_config()
    token = (cfg.MIRON_BOT_TOKEN or "").strip()
    if not token:
        raise SystemExit("MIRON_BOT_TOKEN не задан")

    user_storage = UserStorage(cfg.database_url)
    await user_storage.initialize()
    bot = Bot(token=token)
    feature = MailingFeature(user_storage, bot)
    await feature.initialize()

    try:
        await feature._run_campaign(args.campaign_id)
    finally:
        await feature.teardown()
        await bot.session.close()
        await user_storage.close()

    print(f"done campaign_id={args.campaign_id}")


if __name__ == "__main__":
    asyncio.run(main())
