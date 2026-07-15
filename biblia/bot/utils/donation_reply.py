"""Клавиатура доната / клуба для ответов бота (общая логика messaging и challenge)."""

from __future__ import annotations

import logging
import random
from typing import Optional, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

_CLUB_REF_URL = "https://t.me/Talk_God_Bot?start=ref_202604123451"
_CLUB_BUTTON_TEXT = "Клуб Любящие Бога"
DONATION_CLUB_RANDOM_META_KEY = "__donation_club_random"


def build_random_donation_club_inline_button() -> InlineKeyboardButton:
    """Случайно «Поддержать проект» (callback) или «Клуб Любящие Бога» (url)."""
    if random.randint(1, 2) == 1:
        return InlineKeyboardButton(
            text="💳 Поддержать проект",
            callback_data="payment_start",
        )
    return InlineKeyboardButton(text=_CLUB_BUTTON_TEXT, url=_CLUB_REF_URL)


def donation_club_random_meta_button() -> dict:
    """Маркер в JSON кнопок кампании: при отправке подставить случайную кнопку."""
    return {DONATION_CLUB_RANDOM_META_KEY: True}


def is_donation_club_random_meta(btn: dict) -> bool:
    return bool(btn.get(DONATION_CLUB_RANDOM_META_KEY))


def describe_random_donation_club_button() -> str:
    """Текст для превью рассылки."""
    return (
        "«💳 Поддержать проект» или «Клуб Любящие Бога» "
        "(случайный выбор для каждого получателя)"
    )


async def maybe_donation_keyboard(
    user_storage, user_id: int
) -> Tuple[Optional[InlineKeyboardMarkup], Optional[str]]:
    """
    Решает, показывать ли кнопку доната или клуба.
    Возвращает (keyboard | None, variant | None).
    """
    show_from_mailing = await user_storage.get_and_clear_show_donation_flag(user_id)
    prior_assistant = await user_storage.get_assistant_messages_count(user_id)
    is_first_response = prior_assistant == 0

    should_show = False
    if show_from_mailing:
        should_show = True
        await user_storage.increment_donation_proposal_counter(user_id)
    elif is_first_response:
        logger.info("🎯 Первый ответ для user_id=%s, показываем кнопку доната", user_id)
        should_show = True
    elif random.randint(1, 3) == 1:
        should_show = True

    if not should_show:
        return None, None

    await user_storage.increment_donation_button_counter(user_id)
    btn = build_random_donation_club_inline_button()
    variant = "payment_callback" if btn.callback_data else "club_url"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[btn]])
    return keyboard, variant
