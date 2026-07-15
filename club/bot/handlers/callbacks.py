import logging

from aiogram import Dispatcher, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.features.base import FeatureManager
from bot.filters import PRIVATE_INLINE_CALLBACK_ONLY

logger = logging.getLogger(__name__)


class CallbackHandlers:
    def __init__(self, dp: Dispatcher, feature_manager: FeatureManager):
        self.dp = dp
        self.features = feature_manager

    def register_handlers(self):
        """Регистрирует обработчики callback'ов."""
        self.dp.callback_query.register(
            self._handle_onboarding_callbacks,
            F.data.startswith("onboarding_") & PRIVATE_INLINE_CALLBACK_ONLY,
        )

        self.dp.callback_query.register(
            self._handle_followup_callbacks,
            (F.data.startswith("followup_") | F.data.startswith("self_question_"))
            & PRIVATE_INLINE_CALLBACK_ONLY,
        )

    async def _handle_onboarding_callbacks(self, callback: CallbackQuery, state: FSMContext):
        logger.debug(f"👋 Onboarding callback: {callback.data}")
        onboarding = self.features.get("onboarding")
        await onboarding.handle_callback(callback, state)

    async def _handle_followup_callbacks(self, callback: CallbackQuery, state: FSMContext):
        logger.debug(f"📨 Followup callback: {callback.data}")
        followup = self.features.get("followup")
        if followup:
            await followup.handle_callback(callback, state)
        else:
            await callback.answer("❌ Сервис временно недоступен", show_alert=True)
