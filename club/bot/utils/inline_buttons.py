"""Хелперы для inline-кнопок Telegram."""

from __future__ import annotations

from typing import Optional

from aiogram.types import InlineKeyboardButton


def callback_button(
    text: str,
    callback_data: str,
    *,
    style: Optional[str] = None,
) -> InlineKeyboardButton:
    if style:
        return InlineKeyboardButton(
            text=text,
            callback_data=callback_data,
            style=style,
        )
    return InlineKeyboardButton(text=text, callback_data=callback_data)
