"""Доставка Shorts в топик Telegram."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from aiogram.types import FSInputFile

from youtube_prayer.covers import CoverPack
from youtube_prayer.metadata import VideoMetadata
from youtube_prayer.trends import PrayerTopic

logger = logging.getLogger(__name__)


def _esc(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _esc_pre(s: str) -> str:
    return (s or "").replace("&", "&amp;")


def short_social_caption(meta: VideoMetadata, *, max_len: int = 320) -> str:
    """Короткое описание + хэштеги для подписи к видео."""
    body = (meta.description or "").split("---")[0].strip()
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    short = paras[0] if paras else body
    if len(short) > max_len:
        cut = short[: max_len - 1].rsplit(" ", 1)[0]
        short = (cut or short[: max_len - 1]).rstrip(".,;:!?") + "…"
    tags = " ".join(meta.hashtags[:8]) if meta.hashtags else "#Shorts #молитва"
    return f"{short}\n\n{tags}".strip()


def _cover_vertical_path(covers: Optional[CoverPack]) -> Optional[Path]:
    if covers is None:
        return None
    if covers.vertical and covers.vertical.is_file():
        return covers.vertical
    if covers.horizontal.is_file():
        return covers.horizontal
    return None


def _telegram_video_path(src: Path) -> Path:
    """Telegram лимит ~50 МБ — при необходимости перекодируем."""
    limit = 48 * 1024 * 1024
    if not src.is_file() or src.stat().st_size <= limit:
        return src
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    out = src.with_name(f"{src.stem}_tg.mp4")
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(src),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-movflags",
        "+faststart",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    if proc.returncode == 0 and out.is_file() and out.stat().st_size <= limit:
        logger.info("telegram compress ok %s -> %s bytes", src.name, out.stat().st_size)
        return out
    if proc.returncode == 0 and out.is_file():
        logger.warning("telegram compress still large: %s bytes", out.stat().st_size)
        return out
    logger.warning("telegram compress failed, send original: %s", (proc.stderr or "")[-300:])
    return src


async def deliver_short_pack(
    bot: Any,
    *,
    chat_id: int,
    topic_id: int,
    topic: PrayerTopic,
    day: str,
    index: int,
    vertical: Path,
    covers: Optional[CoverPack] = None,
    metadata: Optional[VideoMetadata] = None,
    premiere_label: str = "",
) -> None:
    yt_title = (metadata.title if metadata else "") or (covers.title if covers else "")
    header = (
        f"📱 <b>YouTube Short</b> · {day} · #{index}\n"
        f"<b>Тренд:</b> {_esc(topic.trend)}\n"
        f"<b>Бриф:</b> {_esc(topic.brief)}\n"
        + (f"<b>Название:</b> {_esc(yt_title)}\n" if yt_title else "")
        + (f"<b>Премьера:</b> {_esc(premiere_label)}\n" if premiere_label else "")
        + f"<b>B-roll:</b> <code>{_esc(topic.broll_query)}</code>"
    )
    kwargs = {"message_thread_id": int(topic_id)} if topic_id else {}
    await bot.send_message(chat_id, header, parse_mode="HTML", **kwargs)

    if metadata is not None:
        blurb = short_social_caption(metadata)
        await bot.send_message(
            chat_id,
            f"<b>Описание</b>\n\n{_esc_pre(blurb)}",
            parse_mode="HTML",
            **kwargs,
        )
        desc = metadata.description_with_hashtags
        if len(desc) > 3900:
            desc = desc[:3800] + "…"
        await bot.send_message(
            chat_id,
            f"<b>Полное описание для YouTube</b>\n\n<pre>{_esc_pre(desc)}</pre>",
            parse_mode="HTML",
            **kwargs,
        )

    cover_path = _cover_vertical_path(covers)
    if cover_path is not None:
        try:
            await bot.send_photo(
                chat_id,
                FSInputFile(str(cover_path), filename="cover_9x16.jpg"),
                caption=(
                    f"🖼 Обложка 9:16 · "
                    f"{_esc((covers.thumbnail_title if covers else '') or yt_title)[:200]}"
                ),
                **kwargs,
            )
        except Exception as e:
            logger.warning("short cover send failed: %s", e)

    cap_title = (yt_title or topic.trend)[:120]
    cap = f"📱 {_esc(cap_title)}"
    if metadata is not None:
        cap = f"{cap}\n\n{_esc_pre(short_social_caption(metadata))}"
    send_path = _telegram_video_path(vertical)
    try:
        await bot.send_video(
            chat_id,
            FSInputFile(str(send_path), filename=send_path.name),
            caption=cap[:1024],
            parse_mode="HTML",
            **kwargs,
        )
    except Exception as e:
        logger.exception("short video send failed: %s", e)
        raise
