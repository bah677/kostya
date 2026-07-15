"""
Клубная сборка Telegram-бота: регистрация фич и хендлеров.

Базовый каркас — :mod:`bot.base_app`.

Класс :class:`TelegramBot` — алиас на ту же сборку (удобство импорта в ``main``).
"""

import logging
from typing import Optional

from bot.base_app import TelegramBotApp
from bot.features.auto_react import AutoReactFeature
from bot.features.benefit import BenefitFeature
from bot.features.admin_console import AdminConsoleFeature
from bot.features.club_digest import ClubDigestFeature
from bot.features.club_scripture_pulse import ClubScripturePulseFeature
from bot.features.club_schedule import ClubScheduleFeature
from bot.features.club_group import ClubGroupFeature
from bot.features.club_outreach_dm import ClubOutreachDmFeature
from bot.features.followup import FollowupFeature
from bot.features.legacy_103_reactivation import Legacy103ReactivationFeature
from bot.features.legal_consent import LegalConsentFeature
from bot.features.gift_activation import GiftActivationFeature
from bot.features.mailing import MailingFeature
from bot.features.media_id_helper import MediaIdHelperFeature
from bot.features.member_gift_extension import MemberGiftExtensionFeature
from bot.features.angel_pool import AngelPoolFeature
from bot.features.wish_board import WishBoardFeature
from bot.features.member_proactive import MemberProactiveFeature
from bot.features.messaging import MessagingFeature
from bot.features.onboarding import OnboardingFeature
from bot.features.payment import PaymentFeature
from bot.features.referral import ReferralFeature
from bot.features.subscription_info import SubscriptionInfoFeature
from bot.features.subscription_reminder import SubscriptionReminderFeature
from bot.features.support import SupportFeature
from bot.features.user_menu import UserMenuFeature
from bot.handlers.callbacks import CallbackHandlers
from bot.handlers.commands import CommandHandlers
from bot.handlers.messages import MessageHandlers
from bot.features import admin_mailing
from bot.features import admin_promo
from bot.payments.fulfillment import PaidOrderFulfillment
from config import config

logger = logging.getLogger(__name__)


class TelegramBot(TelegramBotApp):
    """Клубный процесс бота (polling): полный набор фич клуба."""

    def __init__(
        self,
        *,
        bot_token: Optional[str] = None,
        database_url: Optional[str] = None,
    ):
        super().__init__(bot_token=bot_token, database_url=database_url)

    def _register_features(self) -> None:
        self.order_fulfillment = PaidOrderFulfillment(
            user_storage=self.user_storage,
            bot=self.bot,
            feature_manager=self.feature_manager,
            currency_converter=self.currency_converter,
        )

        followup_feature = FollowupFeature(
            user_storage=self.user_storage,
            bot=self.bot,
            feature_manager=self.feature_manager,
            message_copier=self.message_copier,
        )
        followup_feature.set_rag_stack(self.rag_stack)
        _ds_key = (getattr(config, "DEEPSEEK_API_KEY", None) or "").strip()
        _shared_llm = None
        if _ds_key:
            from openai import AsyncOpenAI

            _shared_llm = AsyncOpenAI(
                api_key=_ds_key,
                base_url="https://api.deepseek.com/v1",
                timeout=60.0,
                max_retries=2,
            )
            followup_feature.set_llm_client(_shared_llm)

        onboarding_feature = OnboardingFeature(
            user_storage=self.user_storage,
            openai_client=self.openai_client,
            feature_manager=self.feature_manager,
            message_copier=self.message_copier,
            interaction_logger=self.interaction_logger,
        )

        messaging_feature = MessagingFeature(
            user_storage=self.user_storage,
            message_copier=self.message_copier,
            feature_manager=self.feature_manager,
        )
        messaging_feature.set_rag_stack(self.rag_stack)

        support_feature = SupportFeature(self.user_storage)

        referral_feature = ReferralFeature(
            user_storage=self.user_storage,
            bot=self.bot,
        )

        payment_feature = PaymentFeature(
            user_storage=self.user_storage,
            yookassa_service=self.yookassa_service,
            bzb_service=self.bzb_service,
            bot=self.bot,
            feature_manager=self.feature_manager,
            order_fulfillment=self.order_fulfillment,
        )

        legal_consent_feature = LegalConsentFeature(
            user_storage=self.user_storage,
            feature_manager=self.feature_manager,
            bot=self.bot,
        )

        club_group_feature = ClubGroupFeature(
            user_storage=self.user_storage,
            bot=self.bot,
            message_copier=self.message_copier,
        )

        club_schedule_feature = ClubScheduleFeature(
            user_storage=self.user_storage,
            bot=self.bot,
        )
        if _shared_llm:
            club_schedule_feature.set_llm_client(_shared_llm)

        club_digest_feature = ClubDigestFeature(
            user_storage=self.user_storage,
            bot=self.bot,
            message_copier=self.message_copier,
        )

        club_scripture_pulse_feature = ClubScripturePulseFeature(
            user_storage=self.user_storage,
            bot=self.bot,
        )

        club_outreach_dm_feature = ClubOutreachDmFeature(
            user_storage=self.user_storage,
            bot=self.bot,
        )

        gift_activation_feature = GiftActivationFeature(
            user_storage=self.user_storage,
            bot=self.bot,
            feature_manager=self.feature_manager,
        )

        auto_react_feature = AutoReactFeature(bot=self.bot)

        subscription_info_feature = SubscriptionInfoFeature(
            user_storage=self.user_storage,
            feature_manager=self.feature_manager,
        )

        mailing_feature = MailingFeature(
            user_storage=self.user_storage,
            bot=self.bot,
            feature_manager=self.feature_manager,
        )

        media_id_helper_feature = MediaIdHelperFeature(
            user_storage=self.user_storage,
            bot=self.bot,
            feature_manager=self.feature_manager,
        )

        benefit_feature = BenefitFeature(
            user_storage=self.user_storage,
            feature_manager=self.feature_manager,
        )

        subscription_reminder_feature = SubscriptionReminderFeature(
            user_storage=self.user_storage,
            bot=self.bot,
            feature_manager=self.feature_manager,
            referral_feature=referral_feature,
        )

        member_proactive_feature = MemberProactiveFeature(
            user_storage=self.user_storage,
            bot=self.bot,
            feature_manager=self.feature_manager,
        )

        admin_console_feature = AdminConsoleFeature(
            user_storage=self.user_storage,
            feature_manager=self.feature_manager,
            message_copier=self.message_copier,
            )

        legacy_103_reactivation_feature = Legacy103ReactivationFeature(
            followup_feature=followup_feature,
            bot=self.bot,
        )

        user_menu_feature = UserMenuFeature(
            user_storage=self.user_storage,
            feature_manager=self.feature_manager,
        )
        member_gift_extension_feature = MemberGiftExtensionFeature(
            user_storage=self.user_storage,
            feature_manager=self.feature_manager,
        )
        wish_board_feature = WishBoardFeature(
            user_storage=self.user_storage,
            feature_manager=self.feature_manager,
        )
        angel_pool_feature = AngelPoolFeature(
            user_storage=self.user_storage,
            feature_manager=self.feature_manager,
        )

        if _shared_llm:
            subscription_reminder_feature.set_llm_client(_shared_llm)
            member_proactive_feature.set_llm_client(_shared_llm)

        subscription_reminder_feature.set_rag_stack(self.rag_stack)
        member_proactive_feature.set_rag_stack(self.rag_stack)

        features = [
            onboarding_feature,
            messaging_feature,
            support_feature,
            followup_feature,
            legacy_103_reactivation_feature,
            referral_feature,
            legal_consent_feature,
            payment_feature,
            auto_react_feature,
            club_group_feature,
            club_schedule_feature,
            club_digest_feature,
            club_scripture_pulse_feature,
            club_outreach_dm_feature,
            gift_activation_feature,
            subscription_info_feature,
            mailing_feature,
            media_id_helper_feature,
            subscription_reminder_feature,
            member_proactive_feature,
            benefit_feature,
            user_menu_feature,
            member_gift_extension_feature,
            wish_board_feature,
            angel_pool_feature,
            admin_console_feature,
        ]

        for feature in features:
            self.feature_manager.register(feature)
            if hasattr(feature, "set_bot"):
                feature.set_bot(self)
            feature.register_handlers(self.dp)

        logger.info("✅ Зарегистрировано %s фич клуба:", len(features))
        for feature in features:
            logger.info("  • %s", feature.name)

        admin_mailing.register_admin_mailing_handlers(self.dp, self.user_storage, self.bot)
        admin_promo.register_admin_promo_handlers(self.dp, self.user_storage, self.bot)

    def _register_handlers(self) -> None:
        command_handlers = CommandHandlers(
            self.dp, self.feature_manager, self.user_storage
        )
        callback_handlers = CallbackHandlers(self.dp, self.feature_manager)
        message_handlers = MessageHandlers(
            self.dp,
            self.feature_manager,
            self.media_processor,
            self.message_copier,
            self.interaction_logger,
        )
        message_handlers.set_bot(self)

        command_handlers.register_handlers()
        callback_handlers.register_handlers()
        message_handlers.register_handlers()


__all__ = ["TelegramBot"]
