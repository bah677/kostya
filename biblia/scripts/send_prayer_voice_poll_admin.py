#!/usr/bin/env python3
"""Разослать админам опрос голосов молитвы (~40 с + кнопки 1–5)."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

p = argparse.ArgumentParser()
p.add_argument("--env", default="/home/appuser/biblia/.env")
_args, _ = p.parse_known_args()
if Path(_args.env).is_file():
    load_dotenv(_args.env, override=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.features.prayer_voice_poll import PrayerVoicePollFeature
from bot.services.prayer_voice_poll import build_poll_voices
from config import config
from storage.user_storage import UserStorage


async def main() -> None:
    args = p.parse_args()
    if Path(args.env).is_file():
        load_dotenv(args.env, override=True)

    token = (os.getenv("BIBLIA_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
    if not token:
        raise SystemExit("Нет BIBLIA_BOT_TOKEN")

    db_url = (
        f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
        f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}"
        f"/{os.getenv('DB_NAME') or os.getenv('BIBLIA_DB_NAME')}"
    )
    storage = UserStorage(db_url)
    await storage.initialize()

    voices = build_poll_voices(config.ELEVENLABS_VOICE_ID or os.getenv("ELEVENLABS_VOICE_ID", ""))
    print(f"Голосов в опросе: {len(voices)}")

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    feature = PrayerVoicePollFeature(storage)

    class _App:
        pass

    app = _App()
    app.bot = bot
    feature.set_bot(app)
    await feature.initialize()

    try:
        sent = await feature.send_poll_to_all_admins()
        print(f"OK sent_to={sent} admins")
    finally:
        await bot.session.close()
        await storage.close()


if __name__ == "__main__":
    asyncio.run(main())
