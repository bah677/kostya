# bot/features/messaging_disabled.py
"""Заглушка MessagingFeature: ИИ-агент временно отключён (бот Насти)."""

import logging
from typing import Optional

from aiogram import Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, User

from bot.features.base import BaseFeature

logger = logging.getLogger(__name__)


class MessagingDisabledFeature(BaseFeature):
    name = "messaging"

    def __init__(self, user_storage, message_copier, feature_manager):
        super().__init__()
        self.user_storage = user_storage
        self.message_copier = message_copier
        self.feature_manager = feature_manager
        self.bot = None

    def set_bot(self, bot):
        self.bot = bot

    def set_rag_stack(self, rag_stack) -> None:
        pass

    async def initialize(self) -> None:
        logger.info("[%s] ИИ-агент отключён (BOT_VARIANT=nastya)", self.name)

    async def teardown(self) -> None:
        logger.info("[%s] Фича остановлена", self.name)

    def register_handlers(self, dp: Dispatcher) -> None:
        pass

    async def handle_chat_message(
        self,
        message: Message,
        state: FSMContext,
        text: str,
        message_id: int = None,
        *,
        from_user: Optional[User] = None,
        from_inline_button: bool = False,
        onboarding_topic_button: bool = False,
    ) -> None:
        logger.debug(
            "[%s] ignored user=%s text_len=%s (agent disabled)",
            self.name,
            message.from_user.id if message.from_user else None,
            len(text or ""),
        )
