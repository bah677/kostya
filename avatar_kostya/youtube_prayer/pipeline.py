"""Оркестратор дневного прогона: RU (3) + EN USA (1) → TG-топик."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional
from zoneinfo import ZoneInfo

from youtube_prayer.audio_pipeline import synthesize_prayer_audio
from youtube_prayer.compose import compose_prayer_for_topic, deepseek_complete
from youtube_prayer.deliver import deliver_run_summary, deliver_topic_pack
from youtube_prayer.render import render_all_shorts, render_horizontal
from youtube_prayer.stock_broll import build_broll_montage
from youtube_prayer.trends import (
    PrayerTopic,
    fetch_google_trends,
    select_prayer_topics,
)
from youtube_prayer.topic_history import append_used_trends, load_recent_trends

logger = logging.getLogger(__name__)

_MSK = ZoneInfo("Europe/Moscow")
_DEFAULT_EN_VOICE = "a4CnuaYbALRvW39mDitg"


@dataclass
class PipelineResult:
    day: str
    ok: bool
    themes: List[str] = field(default_factory=list)
    themes_en: List[str] = field(default_factory=list)
    error: Optional[str] = None
    skipped: bool = False


def _slug(s: str, max_len: int = 40) -> str:
    t = re.sub(r"[^\wа-яёА-ЯЁa-zA-Z0-9]+", "-", (s or "").strip(), flags=re.I)
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


def _apply_broll_env() -> None:
    try:
        from config import config as _cfg

        os.environ.setdefault(
            "YT_PRAYER_BROLL_CLIPS", str(getattr(_cfg, "YT_PRAYER_BROLL_CLIPS", 5))
        )
        os.environ.setdefault(
            "YT_PRAYER_SCENE_SEC", str(getattr(_cfg, "YT_PRAYER_SCENE_SEC", 10))
        )
        os.environ.setdefault(
            "YT_PRAYER_IMAGE_COUNT", str(getattr(_cfg, "YT_PRAYER_IMAGE_COUNT", 2))
        )
        if getattr(_cfg, "YT_PRAYER_IMAGE_GEN", True):
            os.environ.setdefault("YT_PRAYER_IMAGE_GEN", "1")
        os.environ.setdefault(
            "YT_PRAYER_SUBTITLE_OFFSET_SEC",
            str(getattr(_cfg, "YT_PRAYER_SUBTITLE_OFFSET_SEC", -0.35)),
        )
    except Exception:
        pass


async def _run_lang_pack(
    bot: Any,
    *,
    chat_id: int,
    topic_id: int,
    day: str,
    day_dir: Path,
    work_root: Path,
    lang: str,
    geo: str,
    count: int,
    history_days: int,
    voice_id: Optional[str],
    complete_fn,
    notify,
) -> List[str]:
    lang = (lang or "ru").lower()
    label = "EN/US" if lang == "en" else "RU"
    recent = load_recent_trends(work_root, history_days=history_days, lang=lang)
    if recent:
        await notify(
            f"[{label}] история {history_days}д ({len(recent)} тем):\n"
            + "\n".join(f"· {t}" for t in recent[:12])
            + ("\n…" if len(recent) > 12 else "")
        )
    trends = await fetch_google_trends(geo=geo, limit=24)
    topics = await select_prayer_topics(
        trends,
        n=count,
        complete_fn=complete_fn,
        recent_themes=recent,
        lang=lang,
    )
    await notify(f"[{label}] темы:\n" + "\n".join(f"• {t.trend}" for t in topics))

    pack_root = day_dir / lang
    pack_root.mkdir(parents=True, exist_ok=True)
    theme_names: List[str] = []
    summary_lines: List[str] = []
    _apply_broll_env()

    for i, topic in enumerate(topics, 1):
        slug = _slug(topic.trend)
        item_dir = pack_root / f"{i:02d}_{slug}"
        item_dir.mkdir(parents=True, exist_ok=True)
        (item_dir / "topic.json").write_text(
            json.dumps({**asdict(topic), "lang": lang, "geo": geo}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        await notify(f"⏳ [{label} {i}/{len(topics)}] compose: {topic.trend}")

        prayer = await compose_prayer_for_topic(topic, lang=lang)
        if not prayer:
            raise RuntimeError(f"[{label}] не удалось сочинить молитву для «{topic.trend}»")
        (item_dir / "prayer.txt").write_text(prayer, encoding="utf-8")

        await notify(f"🎙 [{label} {i}/{len(topics)}] TTS+фон…")
        wav, ogg, dur, _tts = await synthesize_prayer_audio(
            prayer,
            work_dir=item_dir,
            voice_id=voice_id,
            lang=lang,
        )
        await notify(f"🖼 [{label} {i}/{len(topics)}] b-roll + рендер ({dur:.0f}с)…")

        broll = await build_broll_montage(
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
            lang=lang,
        )
        theme_names.append(topic.trend)
        summary_lines.append(
            f"{label} {i}. {_esc_plain(topic.trend)} — 1×16:9 + {len(shorts)}×9:16"
        )
        logger.info("yt_prayer %s item %s done trend=%r", lang, i, topic.trend)

    await deliver_run_summary(
        bot,
        chat_id=chat_id,
        topic_id=topic_id,
        day=f"{day} [{label}]",
        lines=summary_lines,
    )
    append_used_trends(work_root, day=day, themes=theme_names, lang=lang)
    (pack_root / "done.json").write_text(
        json.dumps(
            {
                "day": day,
                "lang": lang,
                "geo": geo,
                "themes": theme_names,
                "finished_at": datetime.now(_MSK).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return theme_names


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
    en_enabled: bool = True,
    en_count: int = 1,
    en_voice_id: str = _DEFAULT_EN_VOICE,
) -> PipelineResult:
    day = datetime.now(_MSK).strftime("%Y-%m-%d")
    day_dir = run_dir_for_day(work_root, day)
    day_dir.mkdir(parents=True, exist_ok=True)

    if is_done(day_dir) and not force:
        logger.info("yt_prayer already done for %s — skip", day)
        return PipelineResult(day=day, ok=True, skipped=True)

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
        themes_ru = await _run_lang_pack(
            bot,
            chat_id=chat_id,
            topic_id=topic_id,
            day=day,
            day_dir=day_dir,
            work_root=work_root,
            lang="ru",
            geo="RU",
            count=count,
            history_days=history_days,
            voice_id=None,
            complete_fn=_complete,
            notify=_notify,
        )

        themes_en: List[str] = []
        if en_enabled and en_count > 0:
            themes_en = await _run_lang_pack(
                bot,
                chat_id=chat_id,
                topic_id=topic_id,
                day=day,
                day_dir=day_dir,
                work_root=work_root,
                lang="en",
                geo="US",
                count=en_count,
                history_days=history_days,
                voice_id=(en_voice_id or _DEFAULT_EN_VOICE).strip() or _DEFAULT_EN_VOICE,
                complete_fn=_complete,
                notify=_notify,
            )

        mark_done(
            day_dir,
            {
                "day": day,
                "themes": themes_ru,
                "themes_en": themes_en,
                "count_ru": len(themes_ru),
                "count_en": len(themes_en),
                "finished_at": datetime.now(_MSK).isoformat(),
            },
        )
        await _notify(
            f"✅ Готово {day}: RU×{len(themes_ru)}, EN×{len(themes_en)}"
        )
        return PipelineResult(
            day=day, ok=True, themes=themes_ru, themes_en=themes_en
        )
    except Exception as e:
        logger.exception("yt_prayer pipeline failed: %s", e)
        await _notify(f"❌ Ошибка пайплайна: {e}")
        return PipelineResult(day=day, ok=False, error=str(e))


def _esc_plain(s: str) -> str:
    return (s or "").replace("<", "").replace(">", "")
