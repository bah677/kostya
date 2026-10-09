"""Оркестратор дневного прогона: RU (3) + EN USA (1) → TG-топик."""

from __future__ import annotations

from youtube_prayer.langs import profile

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
from youtube_prayer.compose import (
    PrayerComposeIncompleteError,
    compose_prayer_for_topic,
    deepseek_complete,
)
from youtube_prayer.covers import generate_cover_pack
from youtube_prayer.deliver import (
    deliver_pipeline_stopped,
    deliver_topic_pack,
)
from youtube_prayer.youtube_uploader import (
    is_youtube_oauth_error,
    notify_youtube_oauth_problem,
    probe_youtube_oauth,
    upload_premiere_if_enabled,
    YoutubeOAuthError,
)
from youtube_prayer.metadata import generate_video_metadata
from youtube_prayer.render import render_horizontal, format_prayer_theme_label
from youtube_prayer.stock_broll import build_broll_montage
from youtube_prayer.work_cleanup import cleanup_item_media_after_youtube_upload
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
            "YT_PRAYER_BROLL_CLIPS", str(getattr(_cfg, "YT_PRAYER_BROLL_CLIPS", 12))
        )
        os.environ.setdefault(
            "YT_PRAYER_SCENE_SEC", str(getattr(_cfg, "YT_PRAYER_SCENE_SEC", 10))
        )
        os.environ.setdefault(
            "YT_PRAYER_SCENE_POOL", str(getattr(_cfg, "YT_PRAYER_SCENE_POOL", 12))
        )
        os.environ.setdefault(
            "YT_PRAYER_IMAGE_COUNT", str(getattr(_cfg, "YT_PRAYER_IMAGE_COUNT", 0))
        )
        if getattr(_cfg, "YT_PRAYER_IMAGE_GEN", True):
            os.environ.setdefault("YT_PRAYER_IMAGE_GEN", "1")
        if getattr(_cfg, "YT_PRAYER_AI_ANCHORS", True):
            os.environ.setdefault("YT_PRAYER_AI_ANCHORS", "1")
        os.environ.setdefault(
            "YT_PRAYER_AI_ANCHOR_COUNT",
            str(getattr(_cfg, "YT_PRAYER_AI_ANCHOR_COUNT", 8)),
        )
        os.environ.setdefault(
            "YT_PRAYER_COVER_VARIANTS",
            str(getattr(_cfg, "YT_PRAYER_COVER_VARIANTS", 4)),
        )
        os.environ.setdefault(
            "YT_PRAYER_HOOK_SEC",
            str(getattr(_cfg, "YT_PRAYER_HOOK_SEC", 2.0)),
        )
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
    metadata_complete_fn=None,
) -> List[str]:
    lang = (lang or "ru").lower()
    label = profile(lang).label
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

    trend_pool = list(trends)
    meta_fn = metadata_complete_fn or complete_fn

    pack_root = day_dir / lang
    pack_root.mkdir(parents=True, exist_ok=True)
    theme_names: List[str] = []
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

        try:
            prayer = await compose_prayer_for_topic(topic, lang=lang)
        except PrayerComposeIncompleteError as exc:
            err_text = exc.info.telegram_text(
                label=label, index=i, total=len(topics), day=day
            )
            (item_dir / "compose_error.json").write_text(
                json.dumps(
                    {
                        "error": "prayer_incomplete",
                        "lang": exc.info.lang,
                        "trend": exc.info.trend,
                        "brief": exc.info.brief,
                        "reason": exc.info.human_reason(),
                        "attempts": exc.info.attempts,
                        "last_chars": exc.info.last_chars,
                        "last_finish": exc.info.last_finish,
                        "stopped_at": datetime.now(_MSK).isoformat(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            await deliver_pipeline_stopped(
                bot, chat_id=chat_id, topic_id=topic_id, text=err_text
            )
            await notify(
                f"⛔ [{label}] остановлено: молитва неполная «{topic.trend}» "
                f"({exc.info.human_reason()})"
            )
            raise
        (item_dir / "prayer.txt").write_text(prayer, encoding="utf-8")

        await notify(f"📝 [{label} {i}/{len(topics)}] название + описание…")
        meta = await generate_video_metadata(
            trend=topic.trend,
            brief=topic.brief,
            lang=lang,
            complete_fn=meta_fn,
            trend_pool=trend_pool,
            work_dir=item_dir,
        )

        await notify(f"🎙 [{label} {i}/{len(topics)}] TTS+фон…")
        wav, _ogg, dur, _tts, word_timings = await synthesize_prayer_audio(
            prayer,
            work_dir=item_dir,
            voice_id=voice_id,
            lang=lang,
        )
        await notify(f"🖼 [{label} {i}/{len(topics)}] b-roll + обложка + рендер ({dur:.0f}с)…")

        broll = await build_broll_montage(
            item_dir,
            query=topic.broll_query,
            duration_sec=dur,
            prayer_text=prayer,
        )
        covers = await generate_cover_pack(
            item_dir,
            title=meta.title,
            thumbnail_title=meta.thumbnail_title,
            trend=topic.trend,
            brief=topic.brief,
            broll_query=topic.broll_query,
            broll_path=broll,
            hook_question=meta.hook_question,
        )
        if not covers:
            err = (
                f"⛔ <b>Пайплайн остановлен — ошибка</b>\n"
                f"{label} · {day} · ролик {i}/{len(topics)}\n\n"
                f"<b>Тема:</b> {topic.trend}\n"
                f"<b>Причина:</b> не удалось сгенерировать AI-обложку.\n\n"
                f"TTS мог быть готов, видео <b>не собрано</b>.\n"
                f"Перезапуск: <code>/yt_prayer force</code>"
            )
            await deliver_pipeline_stopped(
                bot, chat_id=chat_id, topic_id=topic_id, text=err
            )
            await notify(f"⛔ [{label}] остановлено: нет AI-обложки «{topic.trend}»")
            raise RuntimeError(
                f"[{label}] не удалось сгенерировать AI-обложку для «{topic.trend}»"
            )
        theme_label = format_prayer_theme_label(
            topic.trend, topic.brief, lang=lang
        )
        horizontal = item_dir / "full_16x9.mp4"
        await asyncio.to_thread(
            render_horizontal,
            broll_path=broll,
            audio_wav=wav,
            out_path=horizontal,
            duration_sec=dur,
            theme_label=theme_label,
            work_dir=item_dir,
            prayer_text=prayer,
            word_timings=word_timings,
            hook_question=meta.hook_question,
        )

        await deliver_topic_pack(
            bot,
            chat_id=chat_id,
            topic_id=topic_id,
            topic=topic,
            day=day,
            index=i,
            horizontal=horizontal,
            lang=lang,
            covers=covers,
            metadata=meta,
        )

        yt_note = ""
        try:
            yt = await upload_premiere_if_enabled(
                video_path=horizontal,
                thumbnail_path=covers.horizontal if covers else None,
                metadata=meta,
                lang=lang,
                day=day,
                index=i,
                work_dir=item_dir,
            )
            if yt is not None:
                yt_note = (
                    f"📺 <b>YouTube премьера</b>: {yt.premiere_label}\n"
                    f"<a href=\"{yt.url}\">{yt.url}</a>"
                )
                await bot.send_message(
                    chat_id,
                    yt_note,
                    parse_mode="HTML",
                    message_thread_id=int(topic_id) if topic_id else None,
                    disable_web_page_preview=True,
                )
                # Исходники больше не нужны — освобождаем диск до следующего ролика.
                await asyncio.to_thread(
                    cleanup_item_media_after_youtube_upload, item_dir
                )
        except Exception as e:
            logger.exception("yt_prayer YouTube upload failed trend=%r: %s", topic.trend, e)
            await notify(f"⚠️ YouTube upload failed [{label} {i}]: {e}")
            if is_youtube_oauth_error(e):
                await notify_youtube_oauth_problem(
                    bot,
                    chat_id=chat_id,
                    topic_id=topic_id,
                    detail=str(e),
                )

        theme_names.append(topic.trend)
        logger.info("yt_prayer %s item %s done trend=%r", lang, i, topic.trend)

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

    try:
        probe_youtube_oauth(require_upload_enabled=True)
    except YoutubeOAuthError as e:
        logger.error("yt_prayer OAuth preflight failed: %s", e)
        await notify_youtube_oauth_problem(
            bot,
            chat_id=chat_id,
            topic_id=topic_id,
            detail=str(e),
            force=True,
        )
        await deliver_pipeline_stopped(
            bot,
            chat_id=chat_id,
            topic_id=topic_id,
            text=(
                "⛔ Пайплайн YouTube-молитв не запущен: OAuth токен недействителен.\n"
                "См. алерт выше — нужен перевыпуск."
            ),
        )
        await _notify(f"⛔ OAuth: {e}")
        return PipelineResult(day=day, ok=False, error=str(e))

    async def _complete(system: str, user: str) -> Optional[str]:
        text, _ = await deepseek_complete(system, user, temperature=0.3, max_tokens=1200)
        return text

    async def _complete_metadata(system: str, user: str) -> Optional[str]:
        text, _ = await deepseek_complete(system, user, temperature=0.35, max_tokens=2500)
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
            metadata_complete_fn=_complete_metadata,
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
                metadata_complete_fn=_complete_metadata,
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
    except PrayerComposeIncompleteError as e:
        logger.exception("yt_prayer pipeline stopped (incomplete prayer): %s", e)
        (day_dir / "pipeline_error.json").write_text(
            json.dumps(
                {
                    "error": "prayer_incomplete",
                    "day": day,
                    "message": str(e),
                    "trend": e.info.trend,
                    "reason": e.info.human_reason(),
                    "stopped_at": datetime.now(_MSK).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        await _notify(f"⛔ Пайплайн остановлен: {e}")
        return PipelineResult(day=day, ok=False, error=str(e))
    except Exception as e:
        logger.exception("yt_prayer pipeline failed: %s", e)
        err_generic = (
            f"⛔ <b>Пайплайн остановлен — ошибка</b>\n"
            f"{day}\n\n"
            f"<code>{_esc_plain(str(e))[:500]}</code>\n\n"
            f"Перезапуск: <code>/yt_prayer force</code>"
        )
        try:
            await deliver_pipeline_stopped(
                bot, chat_id=chat_id, topic_id=topic_id, text=err_generic
            )
        except Exception:
            pass
        (day_dir / "pipeline_error.json").write_text(
            json.dumps(
                {
                    "error": "pipeline_failed",
                    "day": day,
                    "message": str(e),
                    "stopped_at": datetime.now(_MSK).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        await _notify(f"⛔ Пайплайн остановлен: {e}")
        return PipelineResult(day=day, ok=False, error=str(e))


def _esc_plain(s: str) -> str:
    return (s or "").replace("<", "").replace(">", "")
