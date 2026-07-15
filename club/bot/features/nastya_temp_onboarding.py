# bot/features/nastya_temp_onboarding.py
"""ВРЕМЕННЫЙ онбординг для бота Насти (BOT_VARIANT=nastya).

Тексты и константы — только здесь, twin_texts не трогаем.
"""

from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.features.base import BaseFeature

logger = logging.getLogger(__name__)

# --- временный сценарий /start (ДР-подарок) ---

BIRTHDAY_VIDEO_NOTE_FILE_ID = (
    "DQACAgIAAxkBAAO8aic6dZq8XnFZlQKIooOSp-nXv-cAAq2hAAJegRBJaqoilVZMa2w7BA"
)

MSG_BIRTHDAY_GIFT = (
    "Друзья, в честь моего ДР я дарю вам доступ на неделю в мой закрытый клуб "
    "«Настоящая Я».\n"
    "Ссылка на моё волшебное пространство:\n"
    "https://t.me/+XV2KIfCgmSthODMy"
)

START_GIFT_LICENSE_DAYS = 9


class NastyaTempOnboardingFeature(BaseFeature):
    name = "nastya_temp_onboarding"

    def __init__(self, user_storage, message_copier=None, feature_manager=None):
        super().__init__()
        self.user_storage = user_storage
        self.message_copier = message_copier
        self.feature_manager = feature_manager
        self.bot = None

    def set_bot(self, bot):
        self.bot = bot

    async def initialize(self) -> None:
        logger.info("[%s] Временный онбординг Насти (ДР-подарок) активен", self.name)

    async def teardown(self) -> None:
        logger.info("[%s] Фича остановлена", self.name)

    def register_handlers(self, _dp: Dispatcher) -> None:
        pass

    async def start_onboarding(
        self,
        user_id: int,
        message: Message,
        state: FSMContext,
        *,
        start_args: str | None = None,
    ) -> None:
        await self.user_storage.save_user_from_message(message)

        await self._run_start_flow(user_id, message, state)

    async def continue_after_consent(self, message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id if message.from_user else 0
        await self._run_start_flow(user_id, message, state)

    async def _run_start_flow(
        self, user_id: int, message: Message, state: FSMContext
    ) -> None:
        await state.clear()

        await self._send_video_note(message)
        await message.answer(MSG_BIRTHDAY_GIFT, disable_web_page_preview=False)
        await self._grant_start_gift_license(user_id)

        await self.user_storage.set_onboarding_complete(user_id)
        logger.info("[%s] /start user=%s (video + gift message + license)", self.name, user_id)

    async def handle_message(
        self, message: Message, state: FSMContext, text: str
    ) -> None:
        logger.debug("[%s] handle_message ignored user=%s", self.name, message.from_user.id)

    async def _send_video_note(self, message: Message) -> None:
        try:
            await message.answer_video_note(video_note=BIRTHDAY_VIDEO_NOTE_FILE_ID)
            logger.info("[%s] video note sent user=%s", self.name, message.from_user.id)
        except TelegramBadRequest as e:
            err = str(e)
            if (
                "VOICE_MESSAGES_FORBIDDEN" in err
                or "voice_messages_forbidden" in err.lower()
            ):
                logger.warning(
                    "[%s] video note forbidden user=%s: %s",
                    self.name,
                    message.from_user.id,
                    err,
                )
                return
            logger.error("[%s] video note TelegramBadRequest user=%s: %s", self.name, message.from_user.id, e)
        except Exception as e:
            logger.error("[%s] video note error user=%s: %s", self.name, message.from_user.id, e)

    async def _grant_start_gift_license(self, user_id: int) -> None:
        license_info = await self.user_storage.get_user_active_license(user_id)
        now = datetime.now()
        if license_info and license_info.get("expires_at") and license_info["expires_at"] > now:
            logger.info(
                "[%s] skip license gift user=%s — already active until %s",
                self.name,
                user_id,
                license_info["expires_at"],
            )
            return

        ok = await self.user_storage.extend_license_by_days(user_id, START_GIFT_LICENSE_DAYS)
        if ok:
            logger.info(
                "[%s] granted %s-day license user=%s",
                self.name,
                START_GIFT_LICENSE_DAYS,
                user_id,
            )
        else:
            logger.error("[%s] failed to grant license user=%s", self.name, user_id)
