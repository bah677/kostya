"""Единый UI для лички: главное меню и редактирование сообщения вместо спама."""

from __future__ import annotations

import logging
from typing import List, Optional, Sequence, Union

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.texts import ru_user_menu as menu_txt

logger = logging.getLogger(__name__)

CB_MAIN_MENU = "menu_act:home"


def main_menu_row() -> List[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(text=menu_txt.BTN_MAIN_MENU, callback_data=CB_MAIN_MENU)
    ]


def _has_main_menu_row(rows: Sequence[Sequence[InlineKeyboardButton]]) -> bool:
    for row in rows:
        for btn in row:
            if (btn.callback_data or "") == CB_MAIN_MENU:
                return True
    return False


def with_main_menu(
    rows: List[List[InlineKeyboardButton]],
    *,
    include: bool = True,
) -> InlineKeyboardMarkup:
    """Добавляет последней строкой «Главное меню»."""
    out = [list(r) for r in rows]
    if include and not _has_main_menu_row(out):
        out.append(main_menu_row())
    return InlineKeyboardMarkup(inline_keyboard=out)


def with_main_menu_markup(
    markup: Optional[InlineKeyboardMarkup],
    *,
    include: bool = True,
) -> Optional[InlineKeyboardMarkup]:
    if markup is None:
        return with_main_menu([], include=include) if include else None
    return with_main_menu(
        [list(row) for row in markup.inline_keyboard],
        include=include,
    )


async def render_user_screen(
    message: Message,
    *,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    edit: bool,
    parse_mode: str = ParseMode.HTML,
    add_main_menu: bool = True,
    disable_web_page_preview: Optional[bool] = None,
) -> None:
    """
    Показать экран в личке: при edit=True — правим текущее сообщение (или заменяем медиа).
    """
    kb = with_main_menu_markup(reply_markup, include=add_main_menu)

    kwargs = {"reply_markup": kb, "parse_mode": parse_mode}
    if disable_web_page_preview is not None:
        kwargs["disable_web_page_preview"] = disable_web_page_preview

    if not edit:
        await message.answer(text, **kwargs)
        return

    if message.photo or message.video_note or message.document:
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
        await message.answer(text, **kwargs)
        return

    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as e:
        err = str(e).lower()
        if "message is not modified" in err:
            return
        logger.warning("render_user_screen edit failed: %s", e)
        await message.answer(text, **kwargs)
