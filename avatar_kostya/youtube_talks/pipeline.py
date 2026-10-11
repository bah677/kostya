"""Разговорные шортсы: «что Писание говорит о …».

Отдельный прогон, который идёт ПОСЛЕ молитвенного и ничего в нём не трогает:
свой рабочий каталог, своя история тем, свой слот премьеры, свой флаг
включения. Если здесь что-то упадёт, молитвы уже выпущены.

Чем отличается от молитвенного ролика:
  текст     развёрнутый ответ из Писания на житейскую беду, а не молитва;
  темп      обычный (1.0) — замедление мешает объяснению;
  длина     не меньше 45 секунд, проверяется по готовому аудио;
  призыв    ведёт в описание канала, а не ссылкой в описании ролика;
  премьера  один слот в день, в промежутке между молитвенными.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional
from zoneinfo import ZoneInfo

from youtube_prayer.audio_pipeline import synthesize_prayer_audio
from youtube_prayer.compose import deepseek_complete
from youtube_prayer.covers import generate_vertical_cover_pack
from youtube_prayer.langs import normalize_lang, profile, setting, voice_id_for
from youtube_prayer.pipeline import is_done, mark_done, run_dir_for_day
from youtube_prayer.render import render_vertical_full
from youtube_prayer.stock_broll import build_broll_montage
from youtube_prayer.topic_history import append_used_trends, load_recent_trends
from youtube_prayer.trends import fetch_google_trends
from youtube_prayer.work_cleanup import cleanup_item_media_after_youtube_upload
from youtube_shorts.metadata import generate_short_metadata, theme_overlay_label
from youtube_shorts.uploader import upload_short_premiere_if_enabled
from youtube_talks import compose as talks_compose
from youtube_talks import prompts as talks_prompts

logger = logging.getLogger(__name__)
_MSK = ZoneInfo("Europe/Moscow")

# Обычный темп: замедление 0.85 нужно молитве, объяснению оно мешает.
TALK_ATEMPO = 1.0


@dataclass
class TalksPipelineResult:
    day: str
    ok: bool
    topics: List[str] = field(default_factory=list)
    error: Optional[str] = None
    skipped: bool = False


def talks_enabled() -> bool:
    raw = str(setting("YT_TALKS_ENABLED", "0") or "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def talks_count(lang: str = "ru") -> int:
    code = normalize_lang(lang)
    name = "YT_TALKS_COUNT" if code == "ru" else f"YT_TALKS_COUNT_{code.upper()}"
    try:
        return max(0, int(setting(name, 1) or 0))
    except (TypeError, ValueError):
        return 0


def talks_work_root(lang: str = "ru") -> Path:
    code = normalize_lang(lang)
    name = "YT_TALKS_WORK_DIR" if code == "ru" else f"YT_TALKS_WORK_DIR_{code.upper()}"
    raw = setting(name, None)
    if raw:
        p = Path(str(raw))
    else:
        suffix = "" if code == "ru" else f"_{code}"
        p = Path(f"data/youtube_talks{suffix}")
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[1] / p
    return p


def talks_premiere_hours(lang: str = "ru") -> List[int]:
    """Один слот в день, в промежутке между молитвенными.

    Ролик живёт ради перехода в Telegram, а переход требует свободных рук:
    вечер дома, а не дорога. У русского канала молитвы в 7, 12, 16, 19 и 22 —
    свободно 20:00. У испанского в 6, 12, 18, 21 и 23 — там 20:00 тоже
    свободно и попадает в вечерний пик.
    """
    from youtube_prayer.premiere_schedule import parse_premiere_hours

    code = normalize_lang(lang)
    name = (
        "YT_TALKS_PREMIERE_HOURS"
        if code == "ru"
        else f"YT_TALKS_PREMIERE_HOURS_{code.upper()}"
    )
    raw = str(setting(name, "") or "").strip()
    return parse_premiere_hours(raw) if raw else [20]


def _slug(s: str, max_len: int = 40) -> str:
    import re

    t = re.sub(r"[^\w]+", "-", (s or "").strip(), flags=re.I)
    t = re.sub(r"-+", "-", t).strip("-").lower()
    return (t or "talk")[:max_len]


async def run_daily_talks_pipeline(
    bot: Any,
    *,
    chat_id: int,
    topic_id: int,
    count: int = 1,
    force: bool = False,
    progress_chat_id: Optional[int] = None,
    history_days: int = 21,
    lang: str = "ru",
) -> TalksPipelineResult:
    lang = normalize_lang(lang)
    prof = profile(lang)
    tag = f"Разговорные {prof.label}"
    day = datetime.now(_MSK).strftime("%Y-%m-%d")
    work_root = talks_work_root(lang)
    day_dir = run_dir_for_day(work_root, day)
    day_dir.mkdir(parents=True, exist_ok=True)

    if is_done(day_dir) and not force:
        logger.info("[talks %s] день %s уже сделан — пропуск", lang, day)
        return TalksPipelineResult(day=day, ok=True, skipped=True)

    async def _notify(text: str) -> None:
        if not progress_chat_id:
            return
        try:
            await bot.send_message(progress_chat_id, text)
        except Exception:
            logger.debug("[talks] уведомление не ушло", exc_info=True)

    async def _complete_meta(system: str, user: str) -> Optional[str]:
        text, _ = await deepseek_complete(system, user, temperature=0.4, max_tokens=1800)
        return text

    done_topics: List[str] = []
    try:
        recent = load_recent_trends(work_root, history_days=history_days, lang=lang)
        trends = await fetch_google_trends(geo=prof.geo, limit=24)
        await _notify(f"🗣 [{tag}] старт, тем в истории: {len(recent)}")

        for i in range(1, max(1, count) + 1):
            topic = await talks_compose.pick_talk_topic(
                lang=lang, trends=trends, recent=recent + done_topics
            )
            await _notify(f"🗣 [{tag} {i}/{count}] тема: {topic.topic}")
            item_dir = day_dir / f"{i:02d}_{_slug(topic.slug_source())}"
            item_dir.mkdir(parents=True, exist_ok=True)

            answer = await talks_compose.compose_answer(topic, lang=lang)
            (item_dir / "answer.txt").write_text(answer, encoding="utf-8")
            spoken = talks_compose.spoken_text(answer, lang=lang)

            meta = await generate_short_metadata(
                trend=topic.topic,
                brief=topic.why or answer[:200],
                complete_fn=_complete_meta,
                work_dir=item_dir,
                day=day,
                index=i,
                lang=lang,
                system_builder=talks_prompts.metadata_system,
                link_lead=talks_prompts.link_lead(lang),
                fallback_builder=talks_prompts.fallback_metadata,
            )

            await _notify(f"🎙 [{tag} {i}/{count}] озвучка…")
            wav, _ogg, dur, _tts, word_timings = await synthesize_prayer_audio(
                spoken,
                work_dir=item_dir,
                voice_id=voice_id_for(lang),
                lang=lang,
                stress_amen=False,
                atempo=TALK_ATEMPO,
            )
            if dur < talks_compose.MIN_SEC:
                # Проверка по готовому аудио, а не по оценке: ровно то, что
                # просили — ролик короче 45 секунд не выпускаем.
                raise talks_compose.TalkAnswerTooShort(
                    f"аудио {dur:.0f} с, нужно минимум {talks_compose.MIN_SEC}"
                )

            await _notify(f"🖼 [{tag} {i}/{count}] видеоряд и рендер ({dur:.0f} с)…")
            broll = await build_broll_montage(
                item_dir,
                query=topic.visual,
                duration_sec=dur,
                prayer_text=answer,
            )
            covers = await generate_vertical_cover_pack(
                item_dir,
                title=meta.title,
                thumbnail_title=meta.thumbnail_title,
                trend=topic.topic,
                brief=topic.why,
                broll_query=topic.visual,
                broll_path=broll,
            )
            vertical = item_dir / "talk_9x16.mp4"
            await asyncio.to_thread(
                render_vertical_full,
                broll_path=broll,
                audio_wav=wav,
                out_path=vertical,
                duration_sec=dur,
                prayer_text=spoken,
                work_dir=item_dir,
                theme_label=theme_overlay_label(meta, topic.topic),
                word_timings=word_timings,
                lang=lang,
            )

            thumb = covers.vertical or covers.horizontal if covers else None
            yt = await upload_short_premiere_if_enabled(
                video_path=vertical,
                thumbnail_path=thumb,
                metadata=meta,
                day=day,
                index=i,
                work_dir=item_dir,
                lang=lang,
                slots_override=talks_premiere_hours(lang),
            )
            if yt is not None:
                await _notify(
                    f"✅ [{tag} {i}/{count}] премьера {yt.premiere_label}\n{yt.url}"
                )
                cleanup_item_media_after_youtube_upload(item_dir)
            else:
                await _notify(
                    f"📦 [{tag} {i}/{count}] готов локально: {vertical.name} "
                    f"({dur:.0f} с). Выгрузка выключена."
                )
            done_topics.append(topic.topic)

        append_used_trends(work_root, day=day, themes=done_topics, lang=lang)
        mark_done(day_dir, {"day": day, "topics": done_topics, "lang": lang})
        return TalksPipelineResult(day=day, ok=True, topics=done_topics)

    except talks_compose.TalkAnswerTooShort as e:
        logger.warning("[talks %s] %s", lang, e)
        await _notify(f"⚠️ [{tag}] пропуск: {e}")
        return TalksPipelineResult(day=day, ok=False, topics=done_topics, error=str(e))
    except Exception as e:
        logger.exception("[talks %s] упал: %s", lang, e)
        await _notify(f"⛔ [{tag}] ошибка: {e}")
        return TalksPipelineResult(day=day, ok=False, topics=done_topics, error=str(e))
