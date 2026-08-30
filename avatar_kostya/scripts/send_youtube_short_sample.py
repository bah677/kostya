#!/usr/bin/env python3
"""Сгенерировать один пример YouTube Short и отправить в личку SUPER_ADMIN."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("send_youtube_short_sample")
_MSK = ZoneInfo("Europe/Moscow")


async def _generate_one_short(work_dir: Path):
    from youtube_prayer.audio_pipeline import synthesize_prayer_audio
    from youtube_prayer.covers import generate_vertical_cover_pack
    from youtube_prayer.compose import deepseek_complete
    from youtube_prayer.render import SHORT_H, SHORT_W, format_prayer_theme_label, render_vertical_full
    from youtube_prayer.stock_broll import build_broll_montage
    from youtube_prayer.trends import PrayerTopic
    from youtube_shorts.compose import compose_short_prayer_for_topic
    from youtube_shorts.metadata import generate_short_metadata

    topic = PrayerTopic(
        trend="тревога и беспокойство",
        brief="Короткая молитва о мире в душе, когда накрывает тревога.",
        broll_query="calm sunrise ocean soft light peaceful",
    )
    work_dir.mkdir(parents=True, exist_ok=True)

    async def _complete(system: str, user: str):
        text, _ = await deepseek_complete(system, user, temperature=0.35, max_tokens=1800)
        return text

    logger.info("compose short prayer…")
    prayer = await compose_short_prayer_for_topic(topic)
    (work_dir / "prayer.txt").write_text(prayer, encoding="utf-8")
    logger.info("prayer chars=%s", len(prayer))

    meta = await generate_short_metadata(
        trend=topic.trend,
        brief=topic.brief,
        complete_fn=_complete,
        work_dir=work_dir,
    )

    logger.info("TTS…")
    wav, _ogg, dur, _, word_timings = await synthesize_prayer_audio(
        prayer,
        work_dir=work_dir,
        voice_id=None,
        lang="ru",
        stress_amen=False,
    )
    logger.info("duration %.1fs", dur)

    logger.info("b-roll 9:16…")
    broll = await build_broll_montage(
        work_dir,
        query=topic.broll_query,
        duration_sec=dur,
        width=SHORT_W,
        height=SHORT_H,
        prayer_text=prayer,
    )

    logger.info("cover 9:16…")
    covers = await generate_vertical_cover_pack(
        work_dir,
        title=meta.title,
        thumbnail_title=meta.thumbnail_title,
        trend=topic.trend,
        brief=topic.brief,
        broll_query=topic.broll_query,
    )

    vertical = work_dir / "sample_short_9x16.mp4"
    theme_label = format_prayer_theme_label(topic.trend, topic.brief, lang="ru")
    logger.info("render vertical theme=%r…", theme_label)
    await asyncio.to_thread(
        render_vertical_full,
        broll_path=broll,
        audio_wav=wav,
        out_path=vertical,
        duration_sec=dur,
        prayer_text=prayer,
        work_dir=work_dir,
        theme_label=theme_label,
        word_timings=word_timings,
    )
    return topic, meta, vertical, covers, prayer, dur


async def main() -> int:
    from aiogram import Bot
    from aiogram.types import FSInputFile

    from config import config, load_biblia_bot_config
    from youtube_shorts.deliver import short_social_caption, _telegram_video_path

    bc = load_biblia_bot_config()
    admin_id = int(getattr(config, "SUPER_ADMIN_ID", 0) or 0)
    if not admin_id:
        logger.error("SUPER_ADMIN_ID не задан")
        return 1

    bot = Bot(token=bc.BIBLIA_BOT_TOKEN)
    day = datetime.now(_MSK).strftime("%Y-%m-%d")
    work = _ROOT / "data" / "youtube_shorts" / "samples" / f"{day}_preview_v2"
    await bot.send_message(
        admin_id,
        "📱 Генерирую обновлённый пример Short (исправлены субтитры, обложка 9:16, Аминь)…",
    )

    try:
        topic, meta, vertical, covers, prayer, dur = await _generate_one_short(work)
    except Exception as e:
        logger.exception("generation failed: %s", e)
        await bot.send_message(admin_id, f"⛔ Не удалось собрать Short: {e}")
        await bot.session.close()
        return 1

    blurb = short_social_caption(meta)
    cap = (
        f"📱 <b>Пример YouTube Short</b> (v2)\n\n"
        f"<b>Тема:</b> {topic.trend}\n"
        f"<b>Название:</b> {meta.title}\n"
        f"<b>Длительность:</b> {dur:.0f} с\n\n"
        f"{blurb}"
    )
    await bot.send_message(admin_id, cap, parse_mode="HTML")

    cover_path = None
    if covers is not None:
        cover_path = covers.vertical or covers.horizontal
    if cover_path and cover_path.is_file():
        await bot.send_photo(
            admin_id,
            FSInputFile(str(cover_path), filename="cover_9x16.jpg"),
            caption="Обложка 9:16 для YouTube",
        )

    tg_video = _telegram_video_path(vertical)
    await bot.send_video(
        admin_id,
        FSInputFile(str(tg_video), filename=tg_video.name),
        caption=meta.title[:200],
    )
    preview = prayer if len(prayer) <= 3500 else prayer[:3500] + "…"
    await bot.send_message(
        admin_id,
        f"<b>Текст молитвы</b>\n\n<pre>{preview}</pre>",
        parse_mode="HTML",
    )
    await bot.session.close()
    logger.info("sent to admin_id=%s path=%s", admin_id, vertical)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
