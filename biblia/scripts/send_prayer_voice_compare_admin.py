#!/usr/bin/env python3
"""Разовая рассылка админам: 2 голосовых молитвы (стандарт + кандидат ElevenLabs)."""

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
from aiogram.types import BufferedInputFile

from bot.features.personal_prayer import _PRAYER_VOICE_SAMPLE_TEXT
from bot.services.elevenlabs_tts import ElevenLabsTTS
from bot.services.prayer_bg_music import mix_voice_with_bg_music
from bot.services.prayer_tts_style import (
    audio_bytes_to_ogg_opus,
    ogg_opus_duration_sec,
    resolve_prayer_tts_atempo,
)
from bot.services.voicebox_tts import format_prayer_for_tts
from storage.user_storage import UserStorage


async def _synthesize(client: ElevenLabsTTS, voice_id: str, text: str) -> bytes | None:
    vid = voice_id.strip()
    if not vid:
        return None
    tempo = resolve_prayer_tts_atempo()
    raw_mp3 = await client.synthesize_ogg_opus(text, voice_id=vid, as_ogg=False)
    mixed = await asyncio.to_thread(
        mix_voice_with_bg_music,
        raw_mp3,
        atempo=tempo,
        voice_suffix=".mp3",
    )
    if mixed:
        return mixed
    return await asyncio.to_thread(
        audio_bytes_to_ogg_opus,
        raw_mp3,
        atempo=tempo,
        prefix="elabs_prayer_",
    )


async def main() -> None:
    args = p.parse_args()
    if Path(args.env).is_file():
        load_dotenv(args.env, override=True)

    token = (os.getenv("BIBLIA_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
    super_id = int(os.getenv("SUPER_ADMIN_ID") or 0)
    if not token or super_id <= 0:
        raise SystemExit("Нет BIBLIA_BOT_TOKEN или SUPER_ADMIN_ID")

    std_id = (os.getenv("ELEVENLABS_VOICE_ID") or "").strip()
    cand_id = (
        os.getenv("ELEVENLABS_CANDIDATE_VOICE_ID") or "a4CnuaYbALRvW39mDitg"
    ).strip()
    if not (os.getenv("ELEVENLABS_API_KEY") or "").strip():
        raise SystemExit("ELEVENLABS_API_KEY не задан")
    if not std_id or not cand_id:
        raise SystemExit("ELEVENLABS_VOICE_ID или кандидат не задан")

    db_url = (
        f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
        f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}"
        f"/{os.getenv('DB_NAME') or os.getenv('BIBLIA_DB_NAME')}"
    )
    storage = UserStorage(db_url)
    await storage.initialize()

    admin_ids: set[int] = {super_id}
    for row in await storage.list_telegram_admin_ids():
        tid = int(row.get("telegram_user_id") or 0)
        if tid > 0:
            admin_ids.add(tid)

    client = ElevenLabsTTS()
    if not client.api_key:
        await storage.close()
        raise SystemExit("ElevenLabs API key пуст")

    tts_text = format_prayer_for_tts(_PRAYER_VOICE_SAMPLE_TEXT)
    print(f"Синтез: std={std_id[:8]}… cand={cand_id[:8]}… chars={len(tts_text)}")
    ogg_std, ogg_cand = await asyncio.gather(
        _synthesize(client, std_id, tts_text),
        _synthesize(client, cand_id, tts_text),
    )
    if not ogg_std and not ogg_cand:
        await storage.close()
        raise SystemExit("Оба TTS вернули пусто")

    intro = (
        "<b>🎧 Выбор голоса для молитвы</b>\n\n"
        "Два варианта озвучки одного текста. Послушайте и напишите, какой ближе.\n\n"
        f"<i>Стандарт:</i> <code>{std_id}</code>\n"
        f"<i>Кандидат:</i> <code>{cand_id}</code>"
    )
    samples = (
        (f"1/2 Стандарт ({std_id[:8]}…)", ogg_std, "prayer_std.ogg"),
        (f"2/2 Кандидат ({cand_id[:8]}…)", ogg_cand, "prayer_cand.ogg"),
    )

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    sent = 0
    try:
        for uid in sorted(admin_ids):
            try:
                await bot.send_message(uid, intro)
                for caption, ogg, filename in samples:
                    if not ogg:
                        await bot.send_message(
                            uid,
                            f"<i>{caption} — не удалось сгенерировать</i>",
                        )
                        continue
                    dur = ogg_opus_duration_sec(ogg)
                    kw: dict = {"caption": caption[:1024]}
                    if dur is not None:
                        kw["duration"] = dur
                    await bot.send_voice(
                        uid,
                        BufferedInputFile(ogg, filename=filename),
                        **kw,
                    )
                sent += 1
                print(f"OK uid={uid}")
            except Exception as e:
                print(f"FAIL uid={uid}: {e}", file=sys.stderr)
    finally:
        await bot.session.close()
        await storage.close()

    print(f"done admins={sent}/{len(admin_ids)}")


if __name__ == "__main__":
    asyncio.run(main())
