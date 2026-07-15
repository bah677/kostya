# bot/features/messaging.py
import logging
from typing import Optional

from aiogram import Dispatcher
from aiogram.enums import ChatType, ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from bot.features.base import BaseFeature
from bot.utils.telegram_html import strip_subscribe_cta
from bot.utils.telegram_html_async import normalize_llm_reply_for_telegram_async

logger = logging.getLogger(__name__)


class MessagingFeature(BaseFeature):
    """Фича обработки сообщений от пользователей с LLM-агентом."""

    name = "messaging"

    def __init__(self, user_storage, message_copier, feature_manager):
        super().__init__()
        self.user_storage = user_storage
        self.message_copier = message_copier
        self.feature_manager = feature_manager
        self.bot = None
        self.agents_client = None

    def set_bot(self, bot):
        """Устанавливает экземпляр бота."""
        self.bot = bot

    async def initialize(self) -> None:
        logger.info("[%s] Фича инициализируется", self.name)
        from openai_client.agents_client import AgentsClient

        self.agents_client = AgentsClient(self.user_storage)
        logger.info("[%s] ✅ Agents client initialized", self.name)

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
    ):
        """Обработчик всех сообщений (только личный чат с ботом)."""
        if message.chat.type != ChatType.PRIVATE:
            logger.debug(
                "messaging: ignore chat_id=%s type=%s",
                message.chat.id,
                message.chat.type,
            )
            return
        user_id = message.from_user.id
        logger.info("📨 Получено сообщение от %s: %s...", user_id, text[:50])

        async def _dialog() -> None:
            agent_response = await self._get_agent_response(user_id, text)
            if agent_response:
                await self._send_to_user(message, agent_response)
            else:
                await message.reply("Что-то пошло не так. Попробуйте еще раз")

        tg = self.bot.bot if self.bot else None
        if tg:
            async with ChatActionSender.typing(
                message.chat.id,
                tg,
                message.message_thread_id,
            ):
                await _dialog()
        else:
            await _dialog()

    async def _get_agent_response(self, user_id: int, question: str) -> Optional[str]:
        try:
            if not self.agents_client:
                logger.warning("Agents client not initialized")
                return None
            return await self.agents_client.run(
                user_message=question,
                user_id=user_id,
            )
        except Exception as e:
            logger.error("❌ Agent response failed for user %s: %s", user_id, e)
            return None

    async def _send_to_user(self, message: Message, response: str) -> None:
        """Ответ пользователю (HTML), без inline-клавиатуры. Маркер CTA из текста убирается."""
        try:
            body, _ = strip_subscribe_cta(response)
            uid = message.from_user.id if message.from_user else 0
            response_html = await normalize_llm_reply_for_telegram_async(
                body,
                user_id=uid,
                agents_client=self.agents_client,
            )
            await message.reply(response_html, parse_mode=ParseMode.HTML)
            logger.info("✅ Agent response sent to user %s", message.from_user.id)
        except Exception as e:
            logger.error("❌ Failed to send response to user: %s", e)
