"""Команды бота «Библия»: поддержка проекта и рефералка без клубных /subs."""

import logging

from aiogram import Dispatcher
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.admin_guard import is_telegram_admin
from bot.features.base import FeatureManager
from bot.features.referral_program import parse_referrer_id_arg

logger = logging.getLogger(__name__)

# Текст после /start (main.py) — правьте константу ниже.
_BIBLIA_WELCOME = (
    "Привет 👋\n"
    "Бог любит тебя и я тоже!\n\n"
    "Я не буду учить тебя жить и раздавать советы, со мной все просто и по-человечески комфортно 🤝\n\n"
    "💬 Здесь тебе не нужно подбирать правильные слова. Просто напиши, что с тобой сейчас происходит, что беспокоит, своими словами, как есть…\n\n"
    "📖 Я подберу слова из Священного Писания и помогу увидеть, как через них Бог отвечает именно в твою ситуацию.🙏\n\n"
    "👉 Также можешь воспользоваться готовыми кнопками запросов в меню, там я собрал самые частые вопросы\n\n📖\n"
    "<blockquote>Придите ко Мне все труждающиеся и обременённые, и Я успокою вас\n\n"
    "<i>(Мф. 11:28)</i></blockquote>"
)


class AppCommandHandlers:
    def __init__(self, dp: Dispatcher, feature_manager: FeatureManager):
        self.dp = dp
        self.features = feature_manager

    def register_handlers(self) -> None:
        self.dp.message.register(self._start_handler, Command(commands=["start"]))
        self.dp.message.register(self._support_handler, Command(commands=["support"]))
        self.dp.message.register(
            self._payment_handler, Command(commands=["payment", "donat"])
        )
        self.dp.message.register(self._affiliate_handler, Command(commands=["affiliate"]))
        self.dp.message.register(
            self._refstats_handler,
            Command(commands=["refstats", "refs", "myrefs"]),
        )
        logger.info(
            "✅ Команды /start /support /payment /donat /affiliate /refstats /refs /myrefs"
        )

    async def _start_handler(self, message: Message, state: FSMContext):
        uid = message.from_user.id if message.from_user else 0
        messaging = self.features.get("messaging")
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
                ref = self.features.get("referral")
                if ref:
                    await ref.register_referral(message, referrer_id_str, is_new_user)
            except Exception as e:
                logger.error("Biblia ref link: %s", e)

        await state.clear()
        await message.answer(_BIBLIA_WELCOME, parse_mode=ParseMode.HTML)

    async def _support_handler(self, message: Message, state: FSMContext):
        support = self.features.get("support")
        await support.start_support(message, state)

    async def _payment_handler(self, message: Message, state: FSMContext):
        pay = self.features.get("payment")
        await pay.show_donation_menu(message, state=state)

    async def _affiliate_handler(self, message: Message, state: FSMContext):
        uid = message.from_user.id if message.from_user else 0
        ref = self.features.get("referral")
        await ref.show_affiliate_link(message, uid)

    async def _refstats_handler(self, message: Message, state: FSMContext):
        uid = message.from_user.id if message.from_user else 0
        ref = self.features.get("referral")
        stor = ref.user_storage

        parts = (message.text or "").split(maxsplit=1)
        arg = parts[1].strip() if len(parts) > 1 else ""

        target_id = uid
        if arg:
            if not await is_telegram_admin(stor, uid):
                await message.answer(
                    "⛔ Смотреть статистику другого user_id могут только админы.\n"
                    "Без аргумента — ваша личная статистика: /refstats",
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
