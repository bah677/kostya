import logging
from aiogram import Dispatcher
from aiogram.enums import ChatType, ParseMode
from aiogram.types import Message
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext

from bot.features.base import FeatureManager
from bot.filters import PRIVATE_CHAT_ONLY
from bot.services.bot_help import build_help_html, resolve_help_tier
from bot.texts.ru_help_hints import (
    HELP_CHAT_HINT_GROUP,
    HELP_CHAT_HINT_PRIVATE,
    SUBS_UNAVAILABLE,
)
from config import config
from storage.user_storage import UserStorage

logger = logging.getLogger(__name__)


class CommandHandlers:
    def __init__(
        self,
        dp: Dispatcher,
        feature_manager: FeatureManager,
        user_storage: UserStorage,
    ):
        self.dp = dp
        self.features = feature_manager
        self.user_storage = user_storage

    def register_handlers(self):
        """Регистрирует обработчики команд."""
        self.dp.message.register(self._help_handler, Command(commands=["help"]))
        self.dp.message.register(
            self._start_handler, PRIVATE_CHAT_ONLY, Command(commands=["start"])
        )
        self.dp.message.register(
            self._support_handler, PRIVATE_CHAT_ONLY, Command(commands=["support"])
        )
        self.dp.message.register(
            self._affiliate_handler, PRIVATE_CHAT_ONLY, Command(commands=["affiliate"])
        )
        self.dp.message.register(
            self._payment_handler, PRIVATE_CHAT_ONLY, Command(commands=["payment"])
        )
        self.dp.message.register(
            self._subs_handler, PRIVATE_CHAT_ONLY, Command(commands=["subs"])
        )
        self.dp.message.register(
            self._menu_handler, PRIVATE_CHAT_ONLY, Command(commands=["menu"])
        )

    async def _start_handler(self, message: Message, state: FSMContext, command: CommandObject):
        """Обработчик для /start"""
        if config.BOT_VARIANT == "nastya":
            onboarding = self.features.get("nastya_temp_onboarding")
        else:
            onboarding = self.features.get("onboarding")
        await onboarding.start_onboarding(
            message.from_user.id, message, state, start_args=command.args
        )

    async def _support_handler(self, message: Message, state: FSMContext):
        """Обработчик для /support"""
        user_id = message.from_user.id

        support = self.features.get("support")
        await support.start_support(message, state)

    async def _affiliate_handler(self, message: Message, state: FSMContext):
        """Обработчик для /affiliate"""
        user_id = message.from_user.id
        referral = self.features.get("referral")
        await referral.show_affiliate_link(message, user_id)

    async def _payment_handler(self, message: Message, state: FSMContext):
        """Обработчик для /payment"""
        user_id = message.from_user.id
        logger.info(f"💰 /payment from user {user_id}")

        payment = self.features.get("payment")
        await payment.show_tariffs(message, state=state)

    async def _subs_handler(self, message: Message, state: FSMContext):
        """Обработчик для /subs"""
        subs_feature = self.features.get("subscription_info")
        if subs_feature:
            await subs_feature.cmd_subs(message, state)
        else:
            await message.answer(SUBS_UNAVAILABLE)

    async def _menu_handler(self, message: Message, state: FSMContext):
        menu = self.features.get("user_menu")
        if menu:
            await menu.cmd_menu(message, state)
        else:
            await message.answer("Меню временно недоступно.")

    async def _help_handler(self, message: Message) -> None:
        if message.from_user is None:
            return
        tier = await resolve_help_tier(self.user_storage, message.from_user.id)
        chat_hint = None
        if message.chat.type == ChatType.PRIVATE:
            chat_hint = HELP_CHAT_HINT_PRIVATE
        elif message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            chat_hint = HELP_CHAT_HINT_GROUP
        text = build_help_html(tier, chat_hint=chat_hint)
        await message.answer(text, parse_mode=ParseMode.HTML)
