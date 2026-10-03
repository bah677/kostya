"""Мини-апп «Контент завод» в боте — только для админов проекта."""

from __future__ import annotations

import logging
from typing import Any, Optional

from aiogram import Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from bot.admin_guard import is_admin_or_super
from bot.features.base import BaseFeature

logger = logging.getLogger(__name__)


def factory_web_url() -> str:
    from config import config

    if not getattr(config, "WEB_ENABLED", False):
        return ""
    domain = str(getattr(config, "WEB_DOMAIN", "") or "").strip()
    if not domain:
        return ""
    return f"https://{domain}/"


def factory_webapp_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть Контент завод",
                    web_app=WebAppInfo(url=url),
                )
            ]
        ]
    )


class ContentFactoryFeature(BaseFeature):
    name = "content_factory"

    def __init__(self, user_storage) -> None:
        super().__init__()
        self.user_storage = user_storage
        self._app: Any = None

    def set_bot(self, app: Any) -> None:
        self._app = app

    def register_handlers(self, dp: Dispatcher) -> None:
        private = F.chat.type == ChatType.PRIVATE
        dp.message.register(self.cmd_factory, private, Command("factory"))
        dp.message.register(self.cmd_factory, private, Command("content_factory"))
        dp.message.register(self.cmd_factory, private, Command("studio"))
        self.log("/factory, /content_factory зарегистрированы")

    async def cmd_factory(self, message: Message) -> None:
        if message.from_user is None or message.from_user.is_bot:
            return
        uid = int(message.from_user.id)
        if not await is_admin_or_super(self.user_storage, uid):
            # Тихо: не светим существование мини-аппа посторонним.
            return

        url = factory_web_url()
        if not url:
            await message.answer(
                "Контент завод сейчас выключен "
                "(WEB_ENABLED / WEB_DOMAIN не настроены)."
            )
            return

        await message.answer(
            "<b>Контент завод</b>\n\n"
            "Мини-приложение для генерации контента: материалы, паспорта, "
            "этапы прогрева, скиллы форматов.\n\n"
            "Доступ только у администраторов проекта. "
            "Внутри Telegram вход по вашему аккаунту автоматически.",
            parse_mode=ParseMode.HTML,
            reply_markup=factory_webapp_keyboard(url),
        )


def prepend_factory_button(
    kb: Optional[InlineKeyboardMarkup],
) -> Optional[InlineKeyboardMarkup]:
    """Добавляет кнопку мини-аппа сверху админ-панели."""
    url = factory_web_url()
    if not url:
        return kb
    row = [
        InlineKeyboardButton(
            text="🏭 Контент завод",
            web_app=WebAppInfo(url=url),
        )
    ]
    if kb is None:
        return InlineKeyboardMarkup(inline_keyboard=[row])
    rows = list(kb.inline_keyboard or [])
    return InlineKeyboardMarkup(inline_keyboard=[row, *rows])
