"""Оркестратор: 8 отдельных вертикальных Shorts в день."""

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
from youtube_prayer.covers import CoverPack, generate_vertical_cover_pack
from youtube_prayer.compose import deepseek_complete
from youtube_prayer.pipeline import is_done, mark_done, run_dir_for_day
from youtube_prayer.langs import (
    attune_text,

    cta_text,
    normalize_lang,
    profile,
    voice_id_for,
)
from youtube_prayer.render import SHORT_H, SHORT_W, format_prayer_theme_label, render_vertical_full
from youtube_prayer.stock_broll import build_broll_montage
from youtube_prayer.topic_history import append_used_trends, load_recent_trends
from youtube_prayer.trends import fetch_google_trends, select_prayer_topics
from youtube_prayer.work_cleanup import cleanup_item_media_after_youtube_upload
from youtube_prayer.youtube_uploader import (
    YoutubeOAuthError,
    is_youtube_oauth_error,
    notify_youtube_oauth_problem,
    probe_youtube_oauth,
)
from youtube_shorts.compose import ShortComposeIncompleteError, compose_short_prayer_for_topic
from youtube_shorts.deliver import deliver_short_pack
from youtube_shorts.metadata import generate_short_metadata, theme_overlay_label
from youtube_shorts.tg_channel_publish import enqueue_short_voice, generate_tg_prayer_tags
from youtube_shorts.uploader import shorts_upload_enabled, upload_short_premiere_if_enabled

logger = logging.getLogger(__name__)

# Сонастройка в начале и призыв в конце звучат голосом и попадают в субтитры.
# prayer.txt остаётся чистой молитвой — обёртки только для озвучки.
# Пустая строка в env отключает соответствующий кусок.
# Многоточие между блоками — пауза для ElevenLabs (чуть дольше обычного абзаца).
_SPOKEN_PAUSE = "..."


def _spoken_prayer(prayer: str, *, lang: str = "ru") -> str:
    """Молитва + сонастройка в начале + CTA в конце (для TTS/субтитров).

    Между блоками — отдельная строка «...»: ElevenLabs держит паузу дольше,
    чем на обычном переносе абзаца.
    """
    parts: List[str] = []
    attune = attune_text(lang)
    if attune:
        parts.append(attune)
    body = (prayer or "").rstrip()
    if body:
        parts.append(body)
    cta = cta_text(lang)
    if cta:
        parts.append(cta)
    if not parts:
        return prayer or ""
    if len(parts) == 1:
        return parts[0]
    glued: List[str] = []
    for i, chunk in enumerate(parts):
        if i:
            glued.append(_SPOKEN_PAUSE)
        glued.append(chunk)
    return "\n\n".join(glued)


_MSK = ZoneInfo("Europe/Moscow")


@dataclass
class ShortsPipelineResult:
    day: str
    ok: bool
    themes: List[str] = field(default_factory=list)
    error: Optional[str] = None
    skipped: bool = False


def _slug(s: str, max_len: int = 40) -> str:
    t = re.sub(r"[^\wа-яёА-ЯЁa-zA-Z0-9]+", "-", (s or "").strip(), flags=re.I)
    t = re.sub(r"-+", "-", t).strip("-").lower()
    return (t or "topic")[:max_len]


def _merge_recent(*lists: List[str]) -> List[str]:
    out: List[str] = []
    for lst in lists:
        for t in lst:
            t = (t or "").strip()
            if t and t not in out:
                out.append(t)
    return out


async def run_daily_youtube_shorts_pipeline(
    bot: Any,
    *,
    chat_id: int,
    topic_id: int,
    work_root: Path,
    count: int = 8,
    force: bool = False,
    progress_chat_id: Optional[int] = None,
    history_days: int = 14,
    horizontal_work_root: Optional[Path] = None,
    lang: str = "ru",
) -> ShortsPipelineResult:
    lang = normalize_lang(lang)
    prof = profile(lang)
    day = datetime.now(_MSK).strftime("%Y-%m-%d")
    day_dir = run_dir_for_day(work_root, day)
    day_dir.mkdir(parents=True, exist_ok=True)

    if is_done(day_dir) and not force:
        logger.info("yt_shorts already done for %s — skip", day)
        return ShortsPipelineResult(day=day, ok=True, skipped=True)

    async def _notify(text: str) -> None:
        if not progress_chat_id:
            return
        try:
            await bot.send_message(progress_chat_id, text)
        except Exception:
            pass

    await _notify(f"📱 YouTube Shorts: старт {day} (×{count}, force={force})")

    if shorts_upload_enabled():
        try:
            probe_youtube_oauth(require_upload_enabled=False, lang=lang)
        except YoutubeOAuthError as e:
            logger.error("yt_shorts OAuth preflight failed: %s", e)
            await notify_youtube_oauth_problem(
                bot,
                chat_id=chat_id,
                topic_id=topic_id,
                detail=str(e),
                force=True,
            )
            await _notify(f"⛔ OAuth: {e}")
            return ShortsPipelineResult(day=day, ok=False, error=str(e))

    async def _complete(system: str, user: str) -> Optional[str]:
        text, _ = await deepseek_complete(system, user, temperature=0.35, max_tokens=1200)
        return text

    async def _complete_metadata(system: str, user: str) -> Optional[str]:
        text, _ = await deepseek_complete(system, user, temperature=0.35, max_tokens=1800)
        return text

    try:
        recent_shorts = load_recent_trends(work_root, history_days=history_days, lang=lang)
        recent_horiz: List[str] = []
        if horizontal_work_root and horizontal_work_root.is_dir():
            recent_horiz = load_recent_trends(
                horizontal_work_root, history_days=history_days, lang=lang
            )
        recent = _merge_recent(recent_shorts, recent_horiz)
        if recent and progress_chat_id:
            await _notify(
                f"[Shorts] история ({len(recent)} тем):\n"
                + "\n".join(f"· {t}" for t in recent[:10])
                + ("\n…" if len(recent) > 10 else "")
            )

        trends = await fetch_google_trends(geo=prof.geo, limit=24)
        topics = await select_prayer_topics(
            trends,
            n=count,
            complete_fn=_complete,
            recent_themes=recent,
            lang=lang,
        )
        await _notify(
            f"[Shorts] темы ({len(topics)}):\n"
            + "\n".join(f"• {t.trend}" for t in topics)
        )

        trend_pool = list(trends)
        theme_names: List[str] = []

        for i, topic in enumerate(topics, 1):
            slug = _slug(topic.trend)
            item_dir = day_dir / f"{i:02d}_{slug}"
            item_dir.mkdir(parents=True, exist_ok=True)
            (item_dir / "topic.json").write_text(
                json.dumps(asdict(topic), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            await _notify(f"⏳ [Short {i}/{len(topics)}] compose: {topic.trend}")

            try:
                prayer = await compose_short_prayer_for_topic(topic, lang=lang)
            except ShortComposeIncompleteError as exc:
                err_text = exc.info.telegram_text(
                    label="Shorts", index=i, total=len(topics), day=day
                )
                (item_dir / "compose_error.json").write_text(
                    json.dumps(
                        {
                            "error": "short_prayer_incomplete",
                            "trend": exc.info.trend,
                            "reason": exc.info.human_reason(),
                            "stopped_at": datetime.now(_MSK).isoformat(),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                await _notify(err_text)
                raise

            (item_dir / "prayer.txt").write_text(prayer, encoding="utf-8")

            meta = await generate_short_metadata(
                trend=topic.trend,
                brief=topic.brief,
                complete_fn=_complete_metadata,
                trend_pool=trend_pool,
                work_dir=item_dir,
                day=day,
                index=i,
                lang=lang,
            )

            # prayer.txt остаётся чистой молитвой; сонастройка + CTA только в озвучке
            spoken = _spoken_prayer(prayer, lang=lang)

            await _notify(f"🎙 [Short {i}/{len(topics)}] TTS…")
            wav, ogg_path, dur, _tts, word_timings = await synthesize_prayer_audio(
                spoken,
                work_dir=item_dir,
                voice_id=voice_id_for(lang),
                lang=lang,
                stress_amen=False,
            )

            await _notify(
                f"🖼 [Short {i}/{len(topics)}] b-roll 9:16 + обложка + рендер ({dur:.0f}с)…"
            )
            broll = await build_broll_montage(
                item_dir,
                query=topic.broll_query,
                duration_sec=dur,
                width=SHORT_W,
                height=SHORT_H,
                prayer_text=spoken,
            )
            covers = await generate_vertical_cover_pack(
                item_dir,
                title=meta.title,
                thumbnail_title=meta.thumbnail_title,
                trend=topic.trend,
                brief=topic.brief,
                broll_query=topic.broll_query,
                broll_path=broll,
            )
            vertical = item_dir / "short_9x16.mp4"
            theme_label = theme_overlay_label(meta, topic.trend)
            await asyncio.to_thread(
                render_vertical_full,
                broll_path=broll,
                audio_wav=wav,
                out_path=vertical,
                duration_sec=dur,
                prayer_text=spoken,
                work_dir=item_dir,
                theme_label=theme_label,
                word_timings=word_timings,
                lang=lang,
            )

            premiere_label = ""
            thumb_path = None
            if covers is not None:
                thumb_path = covers.vertical or covers.horizontal
            yt = await upload_short_premiere_if_enabled(
                video_path=vertical,
                thumbnail_path=thumb_path,
                metadata=meta,
                day=day,
                index=i,
                work_dir=item_dir,
                lang=lang,
            )
            if yt is not None:
                premiere_label = yt.premiere_label

            # Голос в TG-канал — в тот же слот, что премьера на YouTube.
            # Очередь живёт вне item_dir, чтобы cleanup после upload её не снёс.
            try:
                from datetime import datetime as _dt

                publish_at = None
                if yt is not None and getattr(yt, "publish_at_msk", None):
                    try:
                        publish_at = _dt.fromisoformat(str(yt.publish_at_msk))
                    except Exception:
                        publish_at = None
                audio_src = None
                if ogg_path and Path(ogg_path).is_file():
                    audio_src = Path(ogg_path)
                elif wav.is_file():
                    audio_src = wav
                tg_tags = await generate_tg_prayer_tags(
                    prayer,
                    trend=topic.trend,
                    title=meta.title,
                )
                job = enqueue_short_voice(
                    work_root=work_root,
                    item_dir=item_dir,
                    day=day,
                    index=i,
                    title=meta.title,
                    trend=topic.trend,
                    tags=tg_tags,
                    audio_src=audio_src,
                    publish_at_msk=publish_at,
                    lang=lang,
                )
                if job is not None:
                    try:
                        jmeta = json.loads(
                            (job / "job.json").read_text(encoding="utf-8")
                        )
                        slot = jmeta.get("premiere_label") or "?"
                    except Exception:
                        slot = "?"
                    await _notify(f"📣 [Short {i}] голос в канал → {slot}")
            except Exception as e:
                logger.exception("tg voice enqueue failed: %s", e)
                await _notify(f"⚠️ [Short {i}] голос в канал не поставлен: {e}")

            await deliver_short_pack(
                bot,
                chat_id=chat_id,
                topic_id=topic_id,
                topic=topic,
                day=day,
                index=i,
                vertical=vertical,
                covers=covers,
                metadata=meta,
                premiere_label=premiere_label,
            )

            if yt is not None:
                yt_note = (
                    f"📺 <b>Short премьера</b> #{i}: {yt.premiere_label}\n"
                    f'<a href="{yt.url}">{yt.url}</a>'
                )
                kwargs = {"message_thread_id": int(topic_id)} if topic_id else {}
                try:
                    await bot.send_message(
                        chat_id,
                        yt_note,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                        **kwargs,
                    )
                except Exception:
                    pass
                await asyncio.to_thread(
                    cleanup_item_media_after_youtube_upload, item_dir
                )

            theme_names.append(topic.trend)
            logger.info("yt_shorts item %s done trend=%r", i, topic.trend)

        append_used_trends(work_root, day=day, themes=theme_names, lang=lang)
        mark_done(
            day_dir,
            {
                "day": day,
                "themes": theme_names,
                "count": len(theme_names),
                "finished_at": datetime.now(_MSK).isoformat(),
            },
        )
        await _notify(f"✅ Shorts готовы {day}: ×{len(theme_names)}")
        return ShortsPipelineResult(day=day, ok=True, themes=theme_names)

    except ShortComposeIncompleteError as e:
        logger.exception("yt_shorts stopped (incomplete): %s", e)
        await _notify(f"⛔ Shorts остановлены: {e}")
        return ShortsPipelineResult(day=day, ok=False, error=str(e))
    except Exception as e:
        logger.exception("yt_shorts pipeline failed: %s", e)
        await _notify(f"⛔ Shorts ошибка: {e}")
        if is_youtube_oauth_error(e):
            await notify_youtube_oauth_problem(
                bot,
                chat_id=chat_id,
                topic_id=topic_id,
                detail=str(e),
            )
        return ShortsPipelineResult(day=day, ok=False, error=str(e))
