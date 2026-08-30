"""Клавиатура доната / клуба / молитвы / марафона для ответов бота."""

from __future__ import annotations

import logging
import random
from typing import Any, Optional, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.services.crisis_classifier import is_crisis_context

logger = logging.getLogger(__name__)

_CLUB_REF_URL = "https://t.me/Talk_God_Bot?start=ref_202604123451"
_CLUB_BUTTON_TEXT = "Клуб Любящие Бога"
DONATION_CLUB_RANDOM_META_KEY = "__donation_club_random"


def build_random_inline_button() -> InlineKeyboardButton:
    """Случайно: поддержать / клуб / молитва."""
    r = random.randint(1, 3)
    if r == 1:
        return InlineKeyboardButton(
            text="💳 Поддержать проект",
            callback_data="payment_start",
        )
    if r == 2:
        return InlineKeyboardButton(text=_CLUB_BUTTON_TEXT, url=_CLUB_REF_URL)
    return InlineKeyboardButton(
        text="🙏 Помолиться",
        callback_data="prayer_start",
    )


def build_random_donation_club_inline_button() -> InlineKeyboardButton:
    """Совместимость: то же, что build_random_inline_button (3 варианта)."""
    return build_random_inline_button()


def build_marathon_inline_button(name: str) -> InlineKeyboardButton:
    """Синяя кнопка активного марафона."""
    label = (name or "Марафон").strip()[:64] or "Марафон"
    btn = InlineKeyboardButton(text=label, callback_data="marathon_open")
    try:
        btn.style = "primary"
    except Exception:
        pass
    return btn


def donation_club_random_meta_button() -> dict:
    """Маркер в JSON кнопок кампании: при отправке подставить случайную кнопку."""
    return {DONATION_CLUB_RANDOM_META_KEY: True}


def is_donation_club_random_meta(btn: dict) -> bool:
    return bool(btn.get(DONATION_CLUB_RANDOM_META_KEY))


def describe_random_donation_club_button() -> str:
    """Текст для превью рассылки."""
    return (
        "«💳 Поддержать проект», «Клуб Любящие Бога» или «🙏 Помолиться» "
        "(случайный выбор для каждого получателя)"
    )


def _is_commerce_button(btn: InlineKeyboardButton) -> bool:
    cb = (btn.callback_data or "").strip()
    if cb == "prayer_start":
        return False
    if cb == "payment_start" or (btn.url or "").strip():
        return True
    return cb != "prayer_start"


async def maybe_donation_keyboard(
    user_storage,
    user_id: int,
    *,
    text: str = "",
    llm_client: Any = None,
) -> Tuple[Optional[InlineKeyboardMarkup], Optional[str]]:
    """
    Решает, показывать ли кнопку доната / клуба / молитвы / марафона.
    Возвращает (keyboard | None, variant | None).

    Кризис: коммерция (донат/клуб) подавляется → owed_donation_ask;
    кнопка молитвы показывается всегда.
    """
    marathon = await user_storage.get_active_donation_marathon()
    if marathon:
        btn = build_marathon_inline_button(str(marathon.get("name") or "Марафон"))
        try:
            await user_storage.increment_donation_button_counter(user_id)
        except Exception:
            pass
        return InlineKeyboardMarkup(inline_keyboard=[[btn]]), "marathon"

    crisis = False
    if (text or "").strip():
        try:
            crisis = await is_crisis_context(
                user_storage,
                llm_client,
                user_id,
                text,
                point="keyboard",
            )
        except Exception as e:
            logger.warning("crisis check failed uid=%s: %s", user_id, e)
            crisis = True

    owed = False
    try:
        owed = await user_storage.get_owed_donation_ask(user_id)
    except Exception as e:
        logger.debug("get owed_donation_ask uid=%s: %s", user_id, e)

    show_from_mailing = await user_storage.get_and_clear_show_donation_flag(user_id)
    prior_assistant = await user_storage.get_assistant_messages_count(user_id)
    is_first_response = prior_assistant == 0

    should_show = False
    force_owed = False
    if owed and not crisis:
        should_show = True
        force_owed = True
    elif show_from_mailing:
        should_show = True
        try:
            await user_storage.increment_donation_proposal_counter(user_id)
        except Exception:
            pass
    elif is_first_response:
        logger.info("🎯 Первый ответ для user_id=%s, показываем кнопку доната", user_id)
        should_show = True
    elif random.randint(1, 2) == 1:
        should_show = True

    if not should_show:
        if crisis:
            try:
                await user_storage.set_owed_donation_ask(user_id, True)
            except Exception as e:
                logger.debug("set owed on skip uid=%s: %s", user_id, e)
        return None, None

    btn = build_random_inline_button()
    # При owed после кризиса предпочитаем коммерческую кнопку
    if force_owed and not _is_commerce_button(btn):
        btn = InlineKeyboardButton(
            text="💳 Поддержать проект",
            callback_data="payment_start",
        )

    if crisis and _is_commerce_button(btn):
        try:
            await user_storage.set_owed_donation_ask(user_id, True)
        except Exception as e:
            logger.debug("set owed_donation_ask uid=%s: %s", user_id, e)
        return None, None

    # молитва в кризисе — ок; коммерция после owed — сбросить флаг после показа
    if force_owed:
        try:
            await user_storage.set_owed_donation_ask(user_id, False)
        except Exception:
            pass

    if btn.callback_data == "prayer_start":
        variant = "prayer_start"
    elif btn.callback_data:
        variant = "payment_callback"
    else:
        variant = "club_url"

    # счётчик — только после успешного решения показать (вызов снаружи после send —
    # здесь помечаем, что блок готов; increment делает вызывающая сторона после send,
    # либо мы инкрементим здесь как «показали в разметке» — ТЗ: после успешной отправки.
    # Возвращаем keyboard; increment в scripture_messaging после reply.
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[btn]])
    return keyboard, variant
