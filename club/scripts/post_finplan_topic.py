#!/usr/bin/env python3
"""Разовый пост плана «Библия и финансы» в топик клуба + закреп.

  cd /home/appuser/dev/kostya/club
  ./venv/bin/python scripts/post_finplan_topic.py --dry-run
  ./venv/bin/python scripts/post_finplan_topic.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.features.bible_finance import build_finplan_deeplink
from bot.texts import ru_bible_finance as txt
from bot.utils.telegram_identity import resolve_telegram_bot_username
from config import load_config

DEFAULT_PROD_ENV = "/home/appuser/club/.env"
DEFAULT_CHAT_ID = -1003882558802


def _load_config(env_file: str):
    from dotenv import load_dotenv

    load_dotenv(env_file, override=True)
    return load_config()


def _keyboard(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=txt.BTN_GET_PLAN,
                    url=build_finplan_deeplink(bot_username),
                )
            ]
        ]
    )


async def _run(*, env_file: str, dry_run: bool, pin: bool) -> int:
    cfg = _load_config(env_file)
    token = (cfg.MIRON_BOT_TOKEN or "").strip()
    if not token:
        print("Нет MIRON_BOT_TOKEN")
        return 1
    chat_id = int(cfg.CLUB_GROUP_ID or 0) or DEFAULT_CHAT_ID
    topic_id = int(txt.GROUP_TOPIC_ID)
    bot = Bot(token=token)
    try:
        username = await resolve_telegram_bot_username(bot)
        if not username:
            print("Не удалось определить username бота")
            return 1
        html = txt.GROUP_POST_HTML
        keyboard = _keyboard(username)
        print(f"chat_id={chat_id} topic_id={topic_id} bot=@{username}")
        print(html)
        print(f"кнопка: {txt.BTN_GET_PLAN} → {build_finplan_deeplink(username)}")
        if dry_run:
            print("DRY RUN — не отправлял")
            return 0
        msg = await bot.send_message(
            chat_id=chat_id,
            message_thread_id=topic_id,
            text=html,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=keyboard,
        )
        print(f"отправлено message_id={msg.message_id}")
        if pin:
            await bot.pin_chat_message(
                chat_id=chat_id,
                message_id=msg.message_id,
                disable_notification=True,
            )
            print("закреплено")
        return 0
    finally:
        await bot.session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Пост finplan в топик клуба")
    parser.add_argument("--env-file", default=DEFAULT_PROD_ENV)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-pin", action="store_true")
    args = parser.parse_args()
    rc = asyncio.run(
        _run(env_file=args.env_file, dry_run=args.dry_run, pin=not args.no_pin)
    )
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
