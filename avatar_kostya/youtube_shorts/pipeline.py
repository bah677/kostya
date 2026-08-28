"""Оркестратор: 8 отдельных вертикальных Shorts в день."""

from __future__ import annotations

import asyncio
import json
import logging
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
from youtube_prayer.render import SHORT_H, SHORT_W, render_vertical_full
from youtube_prayer.stock_broll import build_broll_montage
from youtube_prayer.topic_history import append_used_trends, load_recent_trends
from youtube_prayer.trends import fetch_google_trends, select_prayer_topics
from youtube_shorts.compose import ShortComposeIncompleteError, compose_short_prayer_for_topic
from youtube_shorts.deliver import deliver_short_pack
from youtube_shorts.metadata import generate_short_metadata
from youtube_shorts.uploader import upload_short_premiere_if_enabled

logger = logging.getLogger(__name__)
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
) -> ShortsPipelineResult:
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

    async def _complete(system: str, user: str) -> Optional[str]:
        text, _ = await deepseek_complete(system, user, temperature=0.35, max_tokens=1200)
        return text

    async def _complete_metadata(system: str, user: str) -> Optional[str]:
        text, _ = await deepseek_complete(system, user, temperature=0.35, max_tokens=1800)
        return text

    try:
        recent_shorts = load_recent_trends(work_root, history_days=history_days, lang="ru")
        recent_horiz: List[str] = []
        if horizontal_work_root and horizontal_work_root.is_dir():
            recent_horiz = load_recent_trends(
                horizontal_work_root, history_days=history_days, lang="ru"
            )
        recent = _merge_recent(recent_shorts, recent_horiz)
        if recent and progress_chat_id:
            await _notify(
                f"[Shorts] история ({len(recent)} тем):\n"
                + "\n".join(f"· {t}" for t in recent[:10])
                + ("\n…" if len(recent) > 10 else "")
            )

        trends = await fetch_google_trends(geo="RU", limit=24)
        topics = await select_prayer_topics(
            trends,
            n=count,
            complete_fn=_complete,
            recent_themes=recent,
            lang="ru",
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
                prayer = await compose_short_prayer_for_topic(topic)
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
            )

            await _notify(f"🎙 [Short {i}/{len(topics)}] TTS…")
            wav, _ogg, dur, _tts = await synthesize_prayer_audio(
                prayer,
                work_dir=item_dir,
                voice_id=None,
                lang="ru",
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
            )
            covers = await generate_vertical_cover_pack(
                item_dir,
                title=meta.title,
                thumbnail_title=meta.thumbnail_title,
                trend=topic.trend,
                brief=topic.brief,
                broll_query=topic.broll_query,
            )
            vertical = item_dir / "short_9x16.mp4"
            await asyncio.to_thread(
                render_vertical_full,
                broll_path=broll,
                audio_wav=wav,
                out_path=vertical,
                duration_sec=dur,
                prayer_text=prayer,
                work_dir=item_dir,
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
            )
            if yt is not None:
                premiere_label = yt.premiere_label

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

            theme_names.append(topic.trend)
            logger.info("yt_shorts item %s done trend=%r", i, topic.trend)

        append_used_trends(work_root, day=day, themes=theme_names, lang="ru")
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
        return ShortsPipelineResult(day=day, ok=False, error=str(e))
