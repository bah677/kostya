"""
Временная сборка бота Насти: свой /start, без ИИ-агента.

Включается через BOT_VARIANT=nastya в .env проекта club_nastya.
"""

import logging
from typing import Optional

from bot.core import TelegramBot
from bot.features.messaging_disabled import MessagingDisabledFeature
from bot.features.nastya_temp_onboarding import NastyaTempOnboardingFeature
from config import config

logger = logging.getLogger(__name__)


class TelegramBotNastya(TelegramBot):
    """Клубный процесс с временным онбордингом Насти и отключённым messaging."""

    def _register_features(self) -> None:
        super()._register_features()

        nastya_onboarding = NastyaTempOnboardingFeature(
            user_storage=self.user_storage,
            message_copier=self.message_copier,
            feature_manager=self.feature_manager,
        )
        messaging_disabled = MessagingDisabledFeature(
            user_storage=self.user_storage,
            message_copier=self.message_copier,
            feature_manager=self.feature_manager,
        )
        messaging_disabled.set_rag_stack(self.rag_stack)

        # Заменяем стандартные onboarding + messaging, остальные фичи без изменений.
        self.feature_manager.unregister("onboarding")
        self.feature_manager.unregister("messaging")

        self.feature_manager.register(nastya_onboarding)
        self.feature_manager.register(messaging_disabled)

        if hasattr(nastya_onboarding, "set_bot"):
            nastya_onboarding.set_bot(self)
        if hasattr(messaging_disabled, "set_bot"):
            messaging_disabled.set_bot(self)

        nastya_onboarding.register_handlers(self.dp)
        messaging_disabled.register_handlers(self.dp)

        logger.info("✅ Nastya temp mode: onboarding=%s, messaging=disabled", nastya_onboarding.name)
        logger.info(
            "Nastya: nightly_audit=%s subscription_reminder=%s member_proactive=%s report_deepseek=%s",
            config.CLUB_GROUP_NIGHTLY_AUDIT_ENABLED,
            config.SUBSCRIPTION_REMINDER_ENABLED,
            config.MEMBER_PROACTIVE_ENABLED,
            config.CLUB_REPORT_INCLUDE_DEEPSEEK,
        )
