# bot/features/auto_react.py
import logging
from aiogram import Dispatcher
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import Message, ReactionTypeEmoji

from bot.features.base import BaseFeature
from config import config

logger = logging.getLogger(__name__)


class AutoReactFeature(BaseFeature):
    """
    Фича для автоматической реакции на сообщения в определенном топике.
    Ставит реакцию-сердце (emoji из whitelist Bot API) на сообщения живых пользователей в топике.

  Важно: регистрируется ДО club_group — aiogram вызывает только первый matching handler;
  после реакции поднимаем SkipHandler, чтобы сработал трекинг активности в club_group.
    """
    
    name = "auto_react"

    #: Символ из whitelist Bot API для setMessageReaction (без FE0F, иначе бывают BAD REQUEST).
    REACTION_EMOJI = "\u2764"
    
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.group_id = config.CLUB_GROUP_ID
        self.react_topic_id = config.REACT_TOPIC_ID
        self.is_active = bool(self.group_id and self.react_topic_id)
    
    async def initialize(self) -> None:
        """Инициализация фичи."""
        if self.is_active:
            logger.info(f"[{self.name}] Фича инициализирована")
            logger.info(f"[{self.name}] Group: {self.group_id}, Topic: {self.react_topic_id}, reaction=u2764 heart")
        else:
            logger.warning(f"[{self.name}] Фича не активна: CLUB_GROUP_ID или REACT_TOPIC_ID не настроены")
    
    async def teardown(self) -> None:
        """Очистка при отключении фичи."""
        logger.info(f"[{self.name}] Фича остановлена")
    
    def register_handlers(self, dp: Dispatcher) -> None:
        """Регистрирует обработчики."""
        if not self.is_active:
            return
        
        dp.message.register(
            self._handle_message,
            lambda m: m.chat.id == self.group_id 
                      and m.message_thread_id == self.react_topic_id
                      and not m.from_user.is_bot
        )
        
        logger.info(f"[{self.name}] Handlers registered for group {self.group_id}, topic {self.react_topic_id}")
    
    async def _handle_message(self, message: Message):
        """Обрабатывает сообщение в указанном топике."""
        try:
            # Ставим реакцию
            await self.bot.set_message_reaction(
                chat_id=message.chat.id,
                message_id=message.message_id,
                reaction=[ReactionTypeEmoji(emoji=self.REACTION_EMOJI)],
            )

            logger.debug(
                "Heart reaction set on msg %s from user %s",
                message.message_id,
                message.from_user.id,
            )
            
        except Exception as e:
            logger.error(f"❌ Failed to add reaction to message {message.message_id}: {e}")
        raise SkipHandler