"""Выбор chat action под тип входящего сообщения (медиапроцессор)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from aiogram import Bot
from aiogram.enums import ChatAction
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

logger = logging.getLogger(__name__)

_CHAT_ACTION_INTERVAL_SEC = 4.5
_CHAT_ACTION_SEND_TIMEOUT_SEC = 5.0


@asynccontextmanager
async def record_voice_chat_action(
    bot: Bot,
    chat_id: int,
    *,
    message_thread_id: int | None = None,
) -> AsyncIterator[None]:
    """
    «Записывает голосовое» без ChatActionSender.__aexit__ — не блокирует
    отправку ответа, если send_chat_action подвис.
    """
    stop = asyncio.Event()

    async def _loop() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(
                    bot.send_chat_action(
                        chat_id=chat_id,
                        action=ChatAction.RECORD_VOICE,
                        message_thread_id=message_thread_id,
                    ),
                    timeout=_CHAT_ACTION_SEND_TIMEOUT_SEC,
                )
            except Exception as e:
                logger.debug("record_voice chat action failed chat=%s: %s", chat_id, e)
            try:
                await asyncio.wait_for(stop.wait(), timeout=_CHAT_ACTION_INTERVAL_SEC)
            except asyncio.TimeoutError:
                pass

    task = asyncio.create_task(_loop())
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def media_processing_chat_action(bot: Bot, message: Message) -> ChatActionSender:
    """
    Статус в чате, пока идёт распознавание/скачивание медиа.
    Голос/аудио — «записывает голос»; фото — загрузка фото; и т.д.
    """
    chat_id = message.chat.id
    thread_id = message.message_thread_id

    if message.voice:
        return ChatActionSender.record_voice(chat_id, bot, thread_id)
    if message.audio:
        return ChatActionSender.upload_voice(chat_id, bot, thread_id)
    if message.video:
        return ChatActionSender.upload_video(chat_id, bot, thread_id)
    if message.video_note:
        return ChatActionSender.record_video_note(chat_id, bot, thread_id)
    if message.photo:
        return ChatActionSender.upload_photo(chat_id, bot, thread_id)
    if message.document:
        return ChatActionSender.upload_document(chat_id, bot, thread_id)
    if message.sticker:
        return ChatActionSender.choose_sticker(chat_id, bot, thread_id)
    if message.location or message.venue:
        return ChatActionSender.find_location(chat_id, bot, thread_id)
    if message.animation:
        return ChatActionSender.upload_video(chat_id, bot, thread_id)

    return ChatActionSender.typing(chat_id, bot, thread_id)
