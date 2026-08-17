"""Отправка Telegram Rich Messages (sendRichMessage / editMessageText)."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Union

from aiogram import Bot
from aiogram.methods.base import TelegramMethod
from aiogram.types import InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)

ChatId = Union[int, str]


class SendRichMessage(TelegramMethod[Message]):
    __returning__ = Message
    __api_method__ = "sendRichMessage"

    chat_id: ChatId
    rich_message: Dict[str, Any]
    reply_markup: Optional[InlineKeyboardMarkup] = None


class EditMessageRich(TelegramMethod[Union[Message, bool]]):
    __returning__ = Message
    __api_method__ = "editMessageText"

    chat_id: ChatId
    message_id: int
    rich_message: Dict[str, Any]
    reply_markup: Optional[InlineKeyboardMarkup] = None


def rich_payload(html: str) -> Dict[str, Any]:
    return {"html": html, "skip_entity_detection": True}


async def edit_rich_message(
    bot: Bot,
    chat_id: ChatId,
    message_id: int,
    html: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    await bot(
        EditMessageRich(
            chat_id=chat_id,
            message_id=message_id,
            rich_message=rich_payload(html),
            reply_markup=reply_markup,
        )
    )


async def send_rich_message(
    bot: Bot,
    chat_id: ChatId,
    html: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> Message:
    return await bot(
        SendRichMessage(
            chat_id=chat_id,
            rich_message=rich_payload(html),
            reply_markup=reply_markup,
        )
    )
