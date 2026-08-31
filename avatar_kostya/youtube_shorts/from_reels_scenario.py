"""Один вертикальный Short из одобренного Reels-сценария → топик YouTube."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Set
from zoneinfo import ZoneInfo

from config import config
from youtube_prayer.audio_pipeline import synthesize_prayer_audio
from youtube_prayer.covers import generate_vertical_cover_pack
from youtube_prayer.compose import deepseek_complete
from youtube_prayer.metadata import VideoMetadata
from youtube_prayer.render import SHORT_H, SHORT_W, render_vertical_full
from youtube_prayer.stock_broll import build_broll_montage
from youtube_prayer.trends import PrayerTopic
from youtube_prayer.work_cleanup import cleanup_item_media_after_youtube_upload
from youtube_shorts.deliver import deliver_short_pack
from youtube_shorts.metadata import _ensure_shorts_title, generate_short_metadata, theme_overlay_label
from youtube_shorts.uploader import upload_short_premiere_if_enabled

logger = logging.getLogger(__name__)
_MSK = ZoneInfo("Europe/Moscow")
_active: Set[str] = set()


def _reel_body(scenario: str) -> str:
    m = re.search(
        r"Текст рилса:\s*(.+?)(?:\n\s*Описание под рилс:|\Z)",
        scenario or "",
        flags=re.S | re.I,
    )
    return (m.group(1) if m else scenario or "").strip()


def _reel_cover(scenario: str) -> str:
    m = re.search(r"Обложка:\s*(.+)", scenario or "", flags=re.I)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1)).strip()[:80]


def _reel_desc_variant(scenario: str, n: int = 1) -> str:
    pat = (
        rf"Вариант\s*{n}\s*\([^)]+\):\s*(.+?)(?=\n\s*—\s*Вариант|\Z)"
        if n
        else r""
    )
    m = re.search(pat, scenario or "", flags=re.S | re.I)
    return (m.group(1).strip() if m else "")[:800]


def _slug(s: str, max_len: int = 40) -> str:
    t = re.sub(r"[^\wа-яёА-ЯЁa-zA-Z0-9]+", "-", (s or "").strip(), flags=re.I)
    t = re.sub(r"-+", "-", t).strip("-").lower()
    return (t or "reels")[:max_len]


def _yt_chat_topic() -> tuple[int, int]:
    chat = int(getattr(config, "YT_PRAYER_CHAT_ID", 0) or 0)
    topic = int(getattr(config, "YT_SHORTS_TOPIC_ID", 0) or 0)
    if not topic:
        topic = int(getattr(config, "YT_PRAYER_TOPIC_ID", 0) or 0)
    return chat, topic


def _work_root() -> Path:
    work = Path(getattr(config, "YT_SHORTS_WORK_DIR", None) or "data/youtube_shorts")
    if not work.is_absolute():
        work = Path(__file__).resolve().parents[1] / work
    return work


def _metadata_from_scenario(
    *, idea_title: str, cover: str, body: str, desc: str
) -> VideoMetadata:
    """Fallback без LLM (если generate_short_metadata недоступен)."""
    title = _ensure_shorts_title(cover or idea_title or "Размышление")
    thumb = (cover or idea_title or title)[:42]
    description = (
        (desc or body[:400]).strip()
        + "\n\n---\n"
        f"Keywords: shorts, вера, духовность, {idea_title}, клуб Разговоры с Богом"
    )
    return VideoMetadata(
        title=title,
        thumbnail_title=thumb,
        description=description,
        hashtags=["#Shorts", "#вера", "#духовность", "#РазговорыСБогом", "#христианство"],
    )


async def _complete_metadata(system: str, user: str) -> Optional[str]:
    text, _ = await deepseek_complete(system, user, temperature=0.4, max_tokens=1800)
    return text


async def _broll_query(idea_title: str, body: str) -> str:
    system = (
        "English stock-video search query, 4–8 words, contemplative spiritual mood. "
        "No people faces if possible. Reply with the query only."
    )
    user = f"Topic: {idea_title}\nScript excerpt: {body[:280]}"
    try:
        text, _ = await deepseek_complete(system, user, temperature=0.2, max_tokens=40)
        q = re.sub(r"[\"'`]", "", (text or "").strip().split("\n")[0])[:80]
        if len(q.split()) >= 2:
            return q
    except Exception as e:
        logger.warning("broll query llm failed: %s", e)
    return "soft candlelight open bible sunrise contemplative"


async def run_reels_scenario_to_youtube_short(
    bot_app: Any,
    scenario_id: uuid.UUID,
    *,
    progress_chat_id: Optional[int] = None,
) -> bool:
    """TTS + b-roll + 9:16 → топик YouTube (как daily Shorts)."""
    sid = str(scenario_id)
    if sid in _active:
        return False
    storage = getattr(bot_app, "user_storage", None)
    bot = getattr(bot_app, "bot", None)
    if not storage or not bot or not hasattr(storage, "get_reels_scenario"):
        return False

    row = await storage.get_reels_scenario(scenario_id)
    if not row:
        return False

    chat_id, topic_id = _yt_chat_topic()
    if not chat_id or not topic_id:
        logger.error("reels→yt: YT_PRAYER_CHAT_ID / topic не заданы")
        return False

    scenario_text = (row.get("scenario_text") or "").strip()
    body = _reel_body(scenario_text)
    if len(body) < 40:
        logger.error("reels→yt: пустой Текст рилса scenario=%s", sid)
        return False

    idea_title = (row.get("idea_title") or row.get("air_title") or "Reels").strip()
    cover = _reel_cover(scenario_text) or idea_title
    desc = _reel_desc_variant(scenario_text, 1) or _reel_desc_variant(scenario_text, 3)
    day = datetime.now(_MSK).strftime("%Y-%m-%d")
    work_dir = _work_root() / "from_reels" / day / f"{sid[:8]}_{_slug(idea_title)}"
    work_dir.mkdir(parents=True, exist_ok=True)

    async def _notify(text: str) -> None:
        if not progress_chat_id:
            return
        try:
            await bot.send_message(progress_chat_id, text)
        except Exception:
            pass

    _active.add(sid)
    try:
        (work_dir / "script.txt").write_text(body, encoding="utf-8")
        await _notify(
            f"🎬 Собираю Short из сценария:\n<b>{idea_title}</b>\n⏳ TTS…"
        )

        wav, _ogg, dur, _tts, word_timings = await synthesize_prayer_audio(
            body,
            work_dir=work_dir,
            voice_id=None,
            lang="ru",
            stress_amen=False,
        )
        broll_q = await _broll_query(idea_title, body)
        topic = PrayerTopic(
            trend=idea_title[:120],
            brief=(desc or body[:200])[:300],
            broll_query=broll_q,
        )
        await _notify(f"🖼 B-roll + обложка + рендер (~{dur:.0f}с)…")
        broll = await build_broll_montage(
            work_dir,
            query=broll_q,
            duration_sec=dur,
            width=SHORT_W,
            height=SHORT_H,
            prayer_text=body,
        )
        meta = await generate_short_metadata(
            trend=idea_title,
            brief=(desc or body[:300]),
            complete_fn=_complete_metadata,
            work_dir=work_dir,
        )
        covers = await generate_vertical_cover_pack(
            work_dir,
            title=meta.title,
            thumbnail_title=meta.thumbnail_title,
            trend=idea_title,
            brief=topic.brief,
            broll_query=broll_q,
            broll_path=broll,
        )
        vertical = work_dir / "short_9x16.mp4"
        theme_label = theme_overlay_label(meta, idea_title)
        await asyncio.to_thread(
            render_vertical_full,
            broll_path=broll,
            audio_wav=wav,
            out_path=vertical,
            duration_sec=dur,
            prayer_text=body,
            work_dir=work_dir,
            theme_label=theme_label,
            word_timings=word_timings,
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
            index=1,
            work_dir=work_dir,
        )
        if yt is not None:
            premiere_label = yt.premiere_label

        await deliver_short_pack(
            bot,
            chat_id=chat_id,
            topic_id=topic_id,
            topic=topic,
            day=day,
            index=1,
            vertical=vertical,
            covers=covers,
            metadata=meta,
            premiere_label=premiere_label,
        )
        if yt is not None:
            try:
                await bot.send_message(
                    chat_id,
                    f"📺 <b>Short премьера</b> (из Reels-сценария)\n"
                    f'<a href="{yt.url}">{yt.url}</a>',
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    message_thread_id=int(topic_id),
                )
            except Exception:
                pass
            await asyncio.to_thread(
                cleanup_item_media_after_youtube_upload, work_dir
            )

        await _notify(f"✅ Short готов и отправлен в топик YouTube.\nТема: {idea_title}")
        logger.info("reels→yt done scenario=%s dur=%.1f", sid, dur)
        return True
    except Exception as e:
        logger.exception("reels→yt failed scenario=%s: %s", sid, e)
        await _notify(f"⛔ Ошибка сборки Short: {e}")
        return False
    finally:
        _active.discard(sid)
