#!/usr/bin/env python3
"""Одноразово: пост+закреп интро доски добрых дел в теме дайджеста (ДДД-6).

Запуск из /home/appuser/dev/kostya/club (или prod после деплоя):

  ./venv/bin/python scripts/post_wish_board_intro.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("post_wish_board_intro")


async def main() -> int:
    from aiogram import Bot

    from bot.services.wish_board_notify import post_board_intro_to_digest
    from config import config

    if not config.wish_board_active:
        logger.error("wish_board_active=false — топики/флаг не настроены")
        return 1

    token = (config.MIRON_BOT_TOKEN or "").strip()
    if not token:
        logger.error("нет MIRON_BOT_TOKEN")
        return 1

    bot = Bot(token=token)
    try:
        msg_id = await post_board_intro_to_digest(bot, pin=True)
        if not msg_id:
            logger.error("не удалось отправить интро")
            return 1
        logger.info("интро отправлено message_id=%s", msg_id)
        return 0
    finally:
        await bot.session.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
