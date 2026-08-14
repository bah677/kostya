"""Доставка роликов в топик закрытой группы аватара."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Optional

from aiogram.types import FSInputFile

from youtube_prayer.covers import CoverPack
from youtube_prayer.metadata import VideoMetadata
from youtube_prayer.trends import PrayerTopic

logger = logging.getLogger(__name__)


async def deliver_topic_pack(
    bot: Any,
    *,
    chat_id: int,
    topic_id: int,
    topic: PrayerTopic,
    day: str,
    index: int,
    prayer_text: str,
    horizontal: Path,
    ogg: Optional[Path] = None,
    lang: str = "ru",
    covers: Optional[CoverPack] = None,
    metadata: Optional[VideoMetadata] = None,
) -> None:
    """Шлёт метаданные + текст + обложку + горизонталь (+ опц. voice) в forum topic."""
    lang_l = (lang or "ru").lower()
    lang_tag = "EN · US" if lang_l == "en" else "RU"
    yt_title = (metadata.title if metadata else "") or (covers.title if covers else "")
    thumb_title = ""
    if metadata is not None:
        thumb_title = metadata.thumbnail_title
    elif covers is not None:
        thumb_title = covers.thumbnail_title
    header = (
        f"🙏 <b>YouTube-молитва</b> · {lang_tag} · {day} · #{index}\n"
        f"<b>Тренд:</b> {_esc(topic.trend)}\n"
        f"<b>Бриф:</b> {_esc(topic.brief)}\n"
        + (f"<b>Название:</b> {_esc(yt_title)}\n" if yt_title else "")
        + (
            f"<b>Обложка:</b> {_esc(thumb_title)}\n"
            if thumb_title and thumb_title != yt_title
            else ""
        )
        + f"<b>B-roll:</b> <code>{_esc(topic.broll_query)}</code>"
    )
    kwargs = {"message_thread_id": int(topic_id)} if topic_id else {}

    await bot.send_message(chat_id, header, parse_mode="HTML", **kwargs)

    if metadata is not None:
        desc = metadata.description_with_hashtags
        if len(desc) > 3900:
            desc = desc[:3800] + "…"
        await bot.send_message(
            chat_id,
            f"<b>Описание для YouTube</b>\n\n<pre>{_esc_pre(desc)}</pre>",
            parse_mode="HTML",
            **kwargs,
        )

    if covers is not None:
        try:
            if covers.horizontal.is_file():
                cap = covers.thumbnail_title or covers.title
                await bot.send_photo(
                    chat_id,
                    FSInputFile(str(covers.horizontal), filename="cover_16x9.jpg"),
                    caption=f"🖼 Обложка 16:9 · {_esc(cap)[:200]}",
                    **kwargs,
                )
        except Exception as e:
            logger.warning("send covers failed: %s", e)

    body = (prayer_text or "").strip()
    if len(body) > 3500:
        body = body[:3400] + "…"
    await bot.send_message(
        chat_id,
        f"<b>Текст молитвы</b>\n\n{_esc(body)}",
        parse_mode="HTML",
        **kwargs,
    )

    if ogg and ogg.is_file() and ogg.stat().st_size < 20_000_000:
        try:
            await bot.send_voice(
                chat_id,
                FSInputFile(str(ogg), filename=f"prayer_{index}.ogg"),
                caption=f"Аудио · #{index} · {_esc(topic.trend)[:60]}",
                **kwargs,
            )
        except Exception as e:
            logger.warning("send_voice failed: %s", e)

    cap_title = yt_title[:80] if yt_title else topic.trend[:80]
    await _send_video_or_doc(
        bot,
        chat_id,
        horizontal,
        caption=f"16:9 · #{index} · {_esc(cap_title)}",
        kwargs=kwargs,
    )


def _esc(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _esc_pre(s: str) -> str:
    return _esc(s)


_TG_BOT_UPLOAD_MAX = 49 * 1024 * 1024  # лимит Bot API ≈ 50 MB


async def _send_video_or_doc(
    bot: Any,
    chat_id: int,
    path: Path,
    *,
    caption: str,
    kwargs: dict,
) -> None:
    if not path.is_file():
        logger.error("missing file %s", path)
        return
    size = path.stat().st_size
    if size >= _TG_BOT_UPLOAD_MAX:
        logger.error(
            "skip send %s: %.1f MB exceeds Telegram bot upload limit (~50MB)",
            path.name,
            size / (1024 * 1024),
        )
        return
    file = FSInputFile(str(path), filename=path.name)
    try:
        if size < 48_000_000:
            await bot.send_video(chat_id, file, caption=caption[:1024], **kwargs)
        else:
            await bot.send_document(chat_id, file, caption=caption[:1024], **kwargs)
    except Exception as e:
        err = str(e).lower()
        if any(
            token in err
            for token in ("too large", "entity too large", "file is too big", "request entity")
        ):
            logger.error("send failed %s (%s bytes): %s", path.name, size, e)
            return
        logger.warning("send_video failed (%s), try document: %s", path.name, e)
        try:
            await bot.send_document(
                chat_id,
                FSInputFile(str(path), filename=path.name),
                caption=caption[:1024],
                **kwargs,
            )
        except Exception as e2:
            logger.error("send_document failed %s: %s", path.name, e2)


async def deliver_run_summary(
    bot: Any,
    *,
    chat_id: int,
    topic_id: int,
    day: str,
    lines: List[str],
) -> None:
    kwargs = {"message_thread_id": int(topic_id)} if topic_id else {}
    text = f"✅ <b>YouTube-молитвы за {day}</b>\n" + "\n".join(lines)
    await bot.send_message(chat_id, text, parse_mode="HTML", **kwargs)


async def deliver_pipeline_stopped(
    bot: Any,
    *,
    chat_id: int,
    topic_id: int,
    text: str,
) -> None:
    """Сообщение в топик YouTube-молитв: пайплайн остановлен по ошибке."""
    kwargs = {"message_thread_id": int(topic_id)} if topic_id else {}
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML", **kwargs)
    except Exception as e:
        logger.error("deliver_pipeline_stopped failed: %s", e)
