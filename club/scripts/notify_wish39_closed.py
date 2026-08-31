#!/usr/bin/env python3
"""Уведомление донору wish #39: просьба закрыта, платить не нужно."""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot
from dotenv import load_dotenv

load_dotenv("/home/appuser/club/.env", override=True)

DONOR_ID = 512742212
TEXT = (
    "Ирина, добрый день!\n\n"
    "По просьбе #39 (продление подписки) коротко обновление:\n\n"
    "Потребность уже закрыта другим путём — участнице продлили месяц "
    "через подарочный код, она снова в клубе.\n\n"
    "Просьбу #39 мы сняли с вас: оплачивать её не нужно, "
    "слот дарителя снова свободен.\n\n"
    "Спасибо, что откликнулись 💛"
)


async def main() -> None:
    token = (os.getenv("MIRON_BOT_TOKEN") or "").strip()
    if not token:
        raise SystemExit("no token")
    bot = Bot(token=token)
    try:
        await bot.send_message(DONOR_ID, TEXT)
        print("OK sent to", DONOR_ID)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
