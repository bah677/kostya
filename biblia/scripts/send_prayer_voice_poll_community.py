#!/usr/bin/env python3
"""Рассылка опроса голосов молитвы активным пользователям (кеш OGG, без повторного синтеза)."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

p = argparse.ArgumentParser(description="Опрос голосов молитвы — топ-30 по голосовым")
p.add_argument("--env", default="/home/appuser/biblia/.env")
p.add_argument(
    "--send",
    action="store_true",
    help="Реально отправить (без флага — только проверка кеша и список получателей)",
)
p.add_argument(
    "--synthesize-if-missing",
    action="store_true",
    help="Если кеша нет — один раз синтезировать и сохранить (иначе ошибка)",
)
p.add_argument(
    "--resynthesize",
    action="store_true",
    help="Пересинтезировать все образцы заново (игнорировать кеш)",
)
_args, _ = p.parse_known_args()
if Path(_args.env).is_file():
    load_dotenv(_args.env, override=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.features.prayer_voice_poll import PrayerVoicePollFeature
from bot.services.prayer_voice_poll import POLL_SLUG, build_poll_voices
from config import config
from storage.user_storage import UserStorage

# Топ-30 по prayer_voice_quota_log; при равенстве — по числу молитв.
COMMUNITY_USER_IDS: tuple[int, ...] = (
    750885748,
    5802975660,
    1243647974,
    406107476,
    423779567,
    6339539615,
    798750809,
    519041279,
    5424778165,
    1289530743,
    604520270,
    1534234693,
    2121217690,
    7645002913,
    5645828290,
    1263200031,
    1464155635,
    517902113,
    1294093437,
    1461107214,
    1019345969,
    662228937,
    152983645,
    1466418104,
    995305642,
    109549534,
    421298318,
    751513419,
    773310349,
    767904530,
)

_EXCLUDED_USER_IDS: frozenset[int] = frozenset({7135176398})

RECIPIENT_USER_IDS: tuple[int, ...] = tuple(
    uid for uid in COMMUNITY_USER_IDS if uid not in _EXCLUDED_USER_IDS
)


async def collect_recipient_ids(storage: UserStorage) -> list[int]:
    ids: set[int] = set(RECIPIENT_USER_IDS)
    sid = int(getattr(config, "SUPER_ADMIN_ID", 0) or os.getenv("SUPER_ADMIN_ID", 0) or 0)
    if sid > 0:
        ids.add(sid)
    for row in await storage.list_telegram_admin_ids():
        tid = int(row.get("telegram_user_id") or 0)
        if tid > 0:
            ids.add(tid)
    ids -= _EXCLUDED_USER_IDS
    return sorted(ids)


async def main() -> None:
    args = p.parse_args()
    if Path(args.env).is_file():
        load_dotenv(args.env, override=True)

    token = (os.getenv("BIBLIA_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
    if not token:
        raise SystemExit("Нет BIBLIA_BOT_TOKEN")

    voices = build_poll_voices(config.ELEVENLABS_VOICE_ID or os.getenv("ELEVENLABS_VOICE_ID", ""))

    db_url = (
        f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
        f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}"
        f"/{os.getenv('DB_NAME') or os.getenv('BIBLIA_DB_NAME')}"
    )
    storage = UserStorage(db_url)
    await storage.initialize()
    recipient_ids = await collect_recipient_ids(storage)

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    feature = PrayerVoicePollFeature(storage)

    class _App:
        pass

    app = _App()
    app.bot = bot
    feature.set_bot(app)
    await feature.initialize()

    print(
        f"Опрос {POLL_SLUG}, голосов: {len(voices)}, "
        f"получателей: {len(recipient_ids)} "
        f"(юзеры {len(RECIPIENT_USER_IDS)} + админы)"
    )

    cache_dir = feature.poll_cache_dir()
    audio = None
    if args.resynthesize:
        print("Пересинтез всех образцов…")
        audio = await feature.synthesize_all()
        if not audio or len(audio) != len(voices):
            raise SystemExit(f"Синтез неполный: {len(audio or {})}/{len(voices)}")
        feature.save_cached_audio(audio)
        print(f"Кеш обновлён: {cache_dir} ({len(audio)} файлов)")
    else:
        audio = feature.load_cached_audio()
        if not audio:
            if args.synthesize_if_missing:
                print(f"Кеш не найден в {cache_dir}, синтезирую один раз…")
                audio = await feature.synthesize_all()
                if not audio or len(audio) != len(voices):
                    raise SystemExit(f"Синтез неполный: {len(audio or {})}/{len(voices)}")
                feature.save_cached_audio(audio)
                print(f"Кеш сохранён: {cache_dir} ({len(audio)} файлов)")
            else:
                raise SystemExit(
                    f"Нет кеша OGG в {cache_dir}. "
                    "Запустите с --synthesize-if-missing или --resynthesize."
                )
        else:
            print(f"Кеш OK: {cache_dir} ({len(audio)} файлов)")

    if not args.send:
        print("DRY-RUN (добавьте --send для рассылки):")
        for uid in recipient_ids:
            print(f"  uid={uid}")
        await bot.session.close()
        await storage.close()
        return

    sent = 0
    failed: list[tuple[int, str]] = []
    try:
        for uid in recipient_ids:
            try:
                await feature.send_poll_to_user(uid, audio_by_idx=audio)
                sent += 1
                print(f"OK uid={uid}")
                await asyncio.sleep(0.3)
            except Exception as e:
                failed.append((uid, str(e)))
                print(f"FAIL uid={uid}: {e}", file=sys.stderr)
    finally:
        await bot.session.close()
        await storage.close()

    print(f"done sent={sent}/{len(recipient_ids)} failed={len(failed)}")


if __name__ == "__main__":
    asyncio.run(main())
