"""Оркестратор дневного прогона: 3 горизонтали + 9 шортсов → TG-топик."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional
from zoneinfo import ZoneInfo

from youtube_prayer.audio_pipeline import synthesize_prayer_audio
from youtube_prayer.compose import compose_prayer_for_topic, deepseek_complete
from youtube_prayer.deliver import deliver_run_summary, deliver_topic_pack
from youtube_prayer.render import render_all_shorts, render_horizontal
from youtube_prayer.stock_broll import obtain_broll
from youtube_prayer.trends import (
    PrayerTopic,
    fetch_google_trends_ru,
    select_prayer_topics,
)
from youtube_prayer.topic_history import append_used_trends, load_recent_trends

logger = logging.getLogger(__name__)

_MSK = ZoneInfo("Europe/Moscow")


@dataclass
class PipelineResult:
    day: str
    ok: bool
    themes: List[str]
    error: Optional[str] = None
    skipped: bool = False


def _slug(s: str, max_len: int = 40) -> str:
    t = re.sub(r"[^\wа-яёА-ЯЁ]+", "-", (s or "").strip(), flags=re.I)
    t = re.sub(r"-+", "-", t).strip("-").lower()
    return (t or "topic")[:max_len]


def run_dir_for_day(base: Path, day: str) -> Path:
    return base / day


def mark_done(day_dir: Path, payload: dict) -> None:
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / "done.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def is_done(day_dir: Path) -> bool:
    return (day_dir / "done.json").is_file()


async def run_daily_youtube_prayer_pipeline(
    bot: Any,
    *,
    chat_id: int,
    topic_id: int,
    work_root: Path,
    count: int = 3,
    force: bool = False,
    progress_chat_id: Optional[int] = None,
    history_days: int = 14,
) -> PipelineResult:
    day = datetime.now(_MSK).strftime("%Y-%m-%d")
    day_dir = run_dir_for_day(work_root, day)
    day_dir.mkdir(parents=True, exist_ok=True)

    if is_done(day_dir) and not force:
        logger.info("yt_prayer already done for %s — skip", day)
        return PipelineResult(day=day, ok=True, themes=[], skipped=True)

    async def _notify(text: str) -> None:
        if not progress_chat_id:
            return
        try:
            await bot.send_message(progress_chat_id, text)
        except Exception:
            pass

    await _notify(f"🎬 YouTube-молитвы: старт {day} (force={force})")

    async def _complete(system: str, user: str) -> Optional[str]:
        text, _ = await deepseek_complete(system, user, temperature=0.3, max_tokens=1200)
        return text

    try:
        recent = load_recent_trends(work_root, history_days=history_days)
        if recent:
            await _notify(
                f"История {history_days}д ({len(recent)} тем), не повторяем:\n"
                + "\n".join(f"· {t}" for t in recent[:15])
                + ("\n…" if len(recent) > 15 else "")
            )
        trends = await fetch_google_trends_ru(limit=24)
        topics = await select_prayer_topics(
            trends,
            n=count,
            complete_fn=_complete,
            recent_themes=recent,
        )
        await _notify(
            "Темы:\n" + "\n".join(f"• {t.trend}" for t in topics)
        )

        summary_lines: List[str] = []
        theme_names: List[str] = []

        for i, topic in enumerate(topics, 1):
            slug = _slug(topic.trend)
            item_dir = day_dir / f"{i:02d}_{slug}"
            item_dir.mkdir(parents=True, exist_ok=True)
            (item_dir / "topic.json").write_text(
                json.dumps(asdict(topic), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await _notify(f"⏳ [{i}/{len(topics)}] compose: {topic.trend}")

            prayer = await compose_prayer_for_topic(topic)
            if not prayer:
                raise RuntimeError(f"не удалось сочинить молитву для «{topic.trend}»")
            (item_dir / "prayer.txt").write_text(prayer, encoding="utf-8")

            await _notify(f"🎙 [{i}/{len(topics)}] TTS+фон…")
            wav, ogg, dur, _tts = await synthesize_prayer_audio(prayer, work_dir=item_dir)
            await _notify(f"🖼 [{i}/{len(topics)}] b-roll + рендер ({dur:.0f}с)…")

            broll = await obtain_broll(
                item_dir, query=topic.broll_query, duration_sec=dur
            )
            horizontal = item_dir / "full_16x9.mp4"
            await asyncio.to_thread(
                render_horizontal,
                broll_path=broll,
                audio_wav=wav,
                out_path=horizontal,
                duration_sec=dur,
            )
            shorts = await asyncio.to_thread(
                render_all_shorts,
                horizontal_path=horizontal,
                prayer_text=prayer,
                duration_sec=dur,
                work_dir=item_dir,
            )

            await deliver_topic_pack(
                bot,
                chat_id=chat_id,
                topic_id=topic_id,
                topic=topic,
                day=day,
                index=i,
                prayer_text=prayer,
                horizontal=horizontal,
                shorts=shorts,
                ogg=ogg,
            )
            theme_names.append(topic.trend)
            summary_lines.append(
                f"{i}. {_esc_plain(topic.trend)} — 1×16:9 + {len(shorts)}×9:16"
            )
            logger.info("yt_prayer item %s done trend=%r", i, topic.trend)

        await deliver_run_summary(
            bot,
            chat_id=chat_id,
            topic_id=topic_id,
            day=day,
            lines=summary_lines,
        )
        mark_done(
            day_dir,
            {
                "day": day,
                "themes": theme_names,
                "count": len(theme_names),
                "finished_at": datetime.now(_MSK).isoformat(),
            },
        )
        append_used_trends(work_root, day=day, themes=theme_names)
        await _notify(f"✅ Готово: {len(theme_names)} пакетов за {day}")
        return PipelineResult(day=day, ok=True, themes=theme_names)
    except Exception as e:
        logger.exception("yt_prayer pipeline failed: %s", e)
        await _notify(f"❌ Ошибка пайплайна: {e}")
        return PipelineResult(day=day, ok=False, themes=[], error=str(e))


def _esc_plain(s: str) -> str:
    return (s or "").replace("<", "").replace(">", "")
