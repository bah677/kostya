"""
Отправка сообщений в админский чат основным ботом (BIBLIA_BOT_TOKEN).
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Union

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile

from bot.utils.telegram_errors import format_exception, is_topic_closed_error
from bot.utils.telegram_html import sanitize_telegram_html, split_telegram_html_message_chunks
from config import config

logger = logging.getLogger(__name__)

_ADMIN_HTML_CHUNK_LEN = 3800


def resolve_admin_service_thread_id() -> Optional[int]:
    """Топик-фолбэк, если General закрыт (TOPIC_CLOSED)."""
    for tid in (
        getattr(config, "BIBLIA_REPORT_THREAD_ID", 0),
        config.PAYMENT_THREAD_ID,
        config.SUPPORT_THREAD_ID,
    ):
        if tid and tid > 0:
            return int(tid)
    return None


def admin_channel_chat_id() -> Optional[Union[int, str]]:
    raw = (config.ADMIN_CHANNEL_ID or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return raw


def resolved_admin_group_id() -> int:
    """Числовой id админ-супергруппы для хендлеров reply."""
    raw = (config.ADMIN_CHANNEL_ID or "").strip()
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


async def send_admin_html_message(
    bot: Bot,
    text: str,
    *,
    thread_id: Optional[int] = None,
    message_thread_id: Optional[int] = None,
    disable_preview: bool = True,
    reply_markup: Any = None,
) -> bool:
    """Отправляет HTML в админ-канал; ``True``, если все фрагменты ушли успешно."""
    last_id = await send_admin_html_message_main_bot(
        bot,
        text,
        thread_id=thread_id,
        message_thread_id=message_thread_id,
        disable_preview=disable_preview,
        reply_markup=reply_markup,
    )
    if last_id is None and (text or "").strip():
        safe = sanitize_telegram_html(text or "")
        if safe.strip():
            return False
    return True


async def send_admin_html_message_main_bot(
    bot: Bot,
    text: str,
    *,
    thread_id: Optional[int] = None,
    message_thread_id: Optional[int] = None,
    disable_preview: bool = True,
    reply_markup: Any = None,
) -> Optional[int]:
    """Возвращает ``message_id`` последнего фрагмента или ``None``."""
    cid = admin_channel_chat_id()
    if cid is None:
        return None

    tid = message_thread_id if message_thread_id is not None else thread_id
    safe = sanitize_telegram_html(text or "")
    if not safe.strip():
        return None

    chunks = split_telegram_html_message_chunks(safe, max_len=_ADMIN_HTML_CHUNK_LEN)
    last_id: Optional[int] = None

    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        markup = reply_markup if i == len(chunks) - 1 else None
        msg_id = await _send_admin_chunk_main_bot(
            bot,
            cid,
            chunk,
            thread_id=tid,
            disable_preview=disable_preview,
            chunk_no=i + 1,
            chunk_total=len(chunks),
            reply_markup=markup,
        )
        if msg_id is None:
            return None
        last_id = msg_id

    return last_id


async def _send_admin_chunk_main_bot(
    bot: Bot,
    cid: Union[int, str],
    chunk: str,
    *,
    thread_id: Optional[int],
    disable_preview: bool,
    chunk_no: int,
    chunk_total: int,
    reply_markup: Any = None,
) -> Optional[int]:
    kwargs: dict[str, Any] = {
        "chat_id": cid,
        "text": chunk,
        "parse_mode": ParseMode.HTML,
        "disable_web_page_preview": disable_preview,
    }
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    use_thread = thread_id is not None and thread_id > 0
    if use_thread:
        kwargs["message_thread_id"] = thread_id

    try:
        msg = await bot.send_message(**kwargs)
        return int(msg.message_id)
    except Exception as e:
        if is_topic_closed_error(e):
            fallback_tid = None if use_thread else resolve_admin_service_thread_id()
            if use_thread:
                logger.warning(
                    "Admin channel: топик %s закрыт (TOPIC_CLOSED), chunk %s/%s → общий чат",
                    thread_id,
                    chunk_no,
                    chunk_total,
                )
                kwargs.pop("message_thread_id", None)
                try:
                    msg = await bot.send_message(**kwargs)
                    return int(msg.message_id)
                except Exception as e2:
                    e = e2
                    fallback_tid = resolve_admin_service_thread_id()
            elif fallback_tid:
                logger.warning(
                    "Admin channel: General закрыт (TOPIC_CLOSED), chunk %s/%s → топик %s",
                    chunk_no,
                    chunk_total,
                    fallback_tid,
                )
                kwargs["message_thread_id"] = fallback_tid
                try:
                    msg = await bot.send_message(**kwargs)
                    return int(msg.message_id)
                except Exception as e2:
                    e = e2
        logger.error(
            "Admin channel send failed chunk %s/%s: %s",
            chunk_no,
            chunk_total,
            format_exception(e),
        )
        return None


async def edit_admin_channel_message(
    bot: Bot,
    *,
    message_id: int,
    text: str,
    chat_id: Optional[Union[int, str]] = None,
    reply_markup: Any = None,
) -> bool:
    cid = chat_id if chat_id is not None else admin_channel_chat_id()
    if cid is None:
        return False
    safe = sanitize_telegram_html(text or "")
    try:
        await bot.edit_message_text(
            chat_id=cid,
            message_id=int(message_id),
            text=safe,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
        return True
    except Exception as e:
        err = str(e).lower()
        if "message is not modified" in err:
            return True
        logger.warning(
            "edit_admin_channel_message failed mid=%s: %s",
            message_id,
            format_exception(e),
        )
        return False


async def _send_admin_media_with_topic_fallback(
    *,
    send_callable,
    kwargs: dict[str, Any],
    thread_id: Optional[int],
    media_label: str,
) -> bool:
    use_thread = thread_id is not None and thread_id > 0
    if use_thread:
        kwargs["message_thread_id"] = thread_id
    try:
        await send_callable(**kwargs)
        return True
    except Exception as e:
        if use_thread and is_topic_closed_error(e):
            logger.warning(
                "Admin channel %s: топик %s закрыт (TOPIC_CLOSED) → общий чат",
                media_label,
                thread_id,
            )
            kwargs.pop("message_thread_id", None)
            try:
                await send_callable(**kwargs)
                return True
            except Exception as e2:
                e = e2
        logger.error("Admin channel %s send failed: %s", media_label, format_exception(e))
        return False


async def send_admin_photo(
    bot: Bot,
    *,
    photo: Any,
    caption: str = "",
    thread_id: Optional[int] = None,
) -> bool:
    cid = admin_channel_chat_id()
    if cid is None:
        return False
    kwargs: dict[str, Any] = {
        "chat_id": cid,
        "photo": photo,
        "caption": caption,
        "parse_mode": ParseMode.HTML,
    }
    return await _send_admin_media_with_topic_fallback(
        send_callable=bot.send_photo,
        kwargs=kwargs,
        thread_id=thread_id,
        media_label="photo",
    )


async def send_admin_video(
    bot: Bot,
    *,
    video: Any,
    caption: str = "",
    thread_id: Optional[int] = None,
) -> bool:
    cid = admin_channel_chat_id()
    if cid is None:
        return False
    kwargs: dict[str, Any] = {
        "chat_id": cid,
        "video": video,
        "caption": caption,
        "parse_mode": ParseMode.HTML,
    }
    return await _send_admin_media_with_topic_fallback(
        send_callable=bot.send_video,
        kwargs=kwargs,
        thread_id=thread_id,
        media_label="video",
    )


async def send_admin_document(
    bot: Bot,
    *,
    document: Any,
    caption: str = "",
    thread_id: Optional[int] = None,
) -> bool:
    cid = admin_channel_chat_id()
    if cid is None:
        return False
    kwargs: dict[str, Any] = {
        "chat_id": cid,
        "document": document,
        "caption": caption,
        "parse_mode": ParseMode.HTML,
    }
    return await _send_admin_media_with_topic_fallback(
        send_callable=bot.send_document,
        kwargs=kwargs,
        thread_id=thread_id,
        media_label="document",
    )


async def send_admin_voice(
    bot: Bot,
    *,
    voice: Any,
    caption: str = "",
    thread_id: Optional[int] = None,
) -> bool:
    cid = admin_channel_chat_id()
    if cid is None:
        return False
    kwargs: dict[str, Any] = {
        "chat_id": cid,
        "voice": voice,
        "caption": caption,
        "parse_mode": ParseMode.HTML,
    }
    return await _send_admin_media_with_topic_fallback(
        send_callable=bot.send_voice,
        kwargs=kwargs,
        thread_id=thread_id,
        media_label="voice",
    )


async def send_admin_audio(
    bot: Bot,
    *,
    audio: Any,
    caption: str = "",
    thread_id: Optional[int] = None,
) -> bool:
    cid = admin_channel_chat_id()
    if cid is None:
        return False
    kwargs: dict[str, Any] = {
        "chat_id": cid,
        "audio": audio,
        "caption": caption,
        "parse_mode": ParseMode.HTML,
    }
    return await _send_admin_media_with_topic_fallback(
        send_callable=bot.send_audio,
        kwargs=kwargs,
        thread_id=thread_id,
        media_label="audio",
    )


async def send_admin_animation(
    bot: Bot,
    *,
    animation: Any,
    caption: str = "",
    thread_id: Optional[int] = None,
) -> bool:
    cid = admin_channel_chat_id()
    if cid is None:
        return False
    kwargs: dict[str, Any] = {
        "chat_id": cid,
        "animation": animation,
        "caption": caption,
        "parse_mode": ParseMode.HTML,
    }
    return await _send_admin_media_with_topic_fallback(
        send_callable=bot.send_animation,
        kwargs=kwargs,
        thread_id=thread_id,
        media_label="animation",
    )


async def send_admin_video_note(
    bot: Bot,
    *,
    video_note: Any,
    thread_id: Optional[int] = None,
) -> bool:
    cid = admin_channel_chat_id()
    if cid is None:
        return False
    kwargs: dict[str, Any] = {"chat_id": cid, "video_note": video_note}
    return await _send_admin_media_with_topic_fallback(
        send_callable=bot.send_video_note,
        kwargs=kwargs,
        thread_id=thread_id,
        media_label="video_note",
    )


async def send_admin_photo_bytes(
    bot: Bot,
    *,
    data: bytes,
    filename: str,
    caption: str,
    thread_id: Optional[int] = None,
) -> bool:
    return await send_admin_photo(
        bot,
        photo=BufferedInputFile(data, filename=filename),
        caption=caption,
        thread_id=thread_id,
    )
