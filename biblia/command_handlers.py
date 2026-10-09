"""Команды бота «Библия»: /start, /menu + алиасы старых команд."""

import logging

from aiogram import Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.admin_guard import is_telegram_admin
from bot.features.base import FeatureManager
from bot.features.referral_program import parse_referrer_id_arg

logger = logging.getLogger(__name__)

_PRIVATE = F.chat.type == ChatType.PRIVATE

# Текст после /start — правьте константу ниже.


class AppCommandHandlers:
    def __init__(self, dp: Dispatcher, feature_manager: FeatureManager):
        self.dp = dp
        self.features = feature_manager

    def register_handlers(self) -> None:
        self.dp.message.register(self._start_handler, Command(commands=["start"]))
        self.dp.message.register(
            self._menu_handler, Command(commands=["menu"]), _PRIVATE
        )
        # Алиасы: старые slash-команды остаются, основной вход — /menu
        self.dp.message.register(
            self._support_handler, Command(commands=["support"]), _PRIVATE
        )
        self.dp.message.register(
            self._payment_handler, Command(commands=["payment", "donat"]), _PRIVATE
        )
        self.dp.message.register(
            self._affiliate_handler, Command(commands=["affiliate"]), _PRIVATE
        )
        self.dp.message.register(
            self._refstats_handler,
            Command(commands=["refstats", "refs", "myrefs"]),
            _PRIVATE,
        )
        logger.info(
            "✅ Команды /start /menu "
            "(алиасы: /support /payment /donat /affiliate /refstats /refs /myrefs)"
        )

    async def _start_handler(self, message: Message, state: FSMContext):
        uid = message.from_user.id if message.from_user else 0
        messaging = self.features.get_optional("messaging")
        stor = getattr(messaging, "user_storage", None)
        if stor is None:
            logger.error("Biblia /start: user_storage недоступен")
            await message.answer("Сервис временно недоступен. Попробуйте позже.")
            return

        args = (message.text or "").split()
        param = args[1] if len(args) > 1 else None

        existing_user = await stor.get_user(uid)
        is_new_user = existing_user is None

        ok = await stor.save_user_from_message(message)
        if not ok:
            logger.warning("Biblia /start: не удалось сохранить пользователя %s", uid)

        if param and param.startswith("ref_"):
            referrer_id_str = param[4:]
            try:
                ref = self.features.get_optional("referral")
                if ref:
                    await ref.register_referral(message, referrer_id_str, is_new_user)
            except Exception as e:
                logger.error("Biblia ref link: %s", e)

        await state.clear()
        from bot.texts import start as _start_texts

        await message.answer(_start_texts().WELCOME, parse_mode=ParseMode.HTML)

        menu = self.features.get_optional("user_menu")
        if menu and message.chat.type == ChatType.PRIVATE:
            await menu.show_menu(message, edit=False)

    async def _menu_handler(self, message: Message, state: FSMContext):
        menu = self.features.get_optional("user_menu")
        if menu:
            await menu.cmd_menu(message, state)
        else:
            await message.answer("Меню временно недоступно.")

    async def _support_handler(self, message: Message, state: FSMContext):
        support = self.features.get_optional("support")
        if not support:
            return
        await support.start_support(message, state)

    async def _payment_handler(self, message: Message, state: FSMContext):
        pay = self.features.get_optional("payment")
        if not pay:
            return
        uid = message.from_user.id if message.from_user else 0
        if uid:
            try:
                await pay.user_storage.set_prayer_voice_unlock_pending(uid, False)
            except Exception:
                pass
        await pay.show_donation_menu(message, state=state)

    async def _affiliate_handler(self, message: Message, state: FSMContext):
        uid = message.from_user.id if message.from_user else 0
        ref = self.features.get_optional("referral")
        if not ref:
            return
        await ref.show_affiliate_link(message, uid)

    async def _refstats_handler(self, message: Message, state: FSMContext):
        uid = message.from_user.id if message.from_user else 0
        ref = self.features.get_optional("referral")
        if not ref:
            return
        stor = ref.user_storage

        parts = (message.text or "").split(maxsplit=1)
        arg = parts[1].strip() if len(parts) > 1 else ""

        target_id = uid
        if arg:
            if not await is_telegram_admin(stor, uid):
                await message.answer(
                    "⛔ Смотреть статистику другого user_id могут только админы.\n"
                    "Без аргумента — ваша личная статистика: /menu → реферальная статистика",
                    parse_mode=ParseMode.HTML,
                )
                return
            parsed = parse_referrer_id_arg(arg)
            if parsed is None:
                await message.answer(
                    "❌ Не понял user_id. Пример:\n"
                    "<code>/refstats 304631563</code>\n"
                    "<code>/refstats ref_304631563</code>",
                    parse_mode=ParseMode.HTML,
                )
                return
            target_id = parsed

        await ref.show_referral_stats(message, target_id, viewer_id=uid)
