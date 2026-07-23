"""Сборка Telegram-бота: фичи и регистрация хендлеров."""

import logging
from typing import Optional

from bot.base_app import TelegramBotApp
from bot.features.daily_admin_report import DailyAdminReportFeature
from bot.features.donation_marathon import DonationMarathonFeature
from bot.features.donation_payment import DonationPaymentFeature
from bot.features.frequent_questions import FrequentQuestionsFeature
from bot.features.mailing import MailingFeature
from bot.features.media_id_helper import MediaIdHelperFeature
from bot.features.personal_prayer import PersonalPrayerFeature
from bot.features.scripture_challenge import ScriptureChallengeFeature
from bot.features.scripture_challenge_scheduler import ScriptureChallengeSchedulerFeature
from bot.features.referral_program import ReferralProgramFeature
from bot.features.scheduled_mailing import ScheduledMailingFeature
from bot.features.scripture_encouragement_mailing import ScriptureEncouragementMailingFeature
from bot.features.scripture_messaging import ScriptureMessagingFeature
from bot.features.support import SupportFeature
from bot.features import admin_mailing
from bot.handlers.messages import MessageHandlers

from command_handlers import AppCommandHandlers
from config import BibliaBotConfig, load_biblia_bot_config
from openai_client.agents_client import AgentsClient

logger = logging.getLogger(__name__)


class BotApplication(TelegramBotApp):
    """Диалог, поддержка, оплаты/заказы, рефералка, рассылки, FAQ по /more."""

    def __init__(self, biblia_cfg: Optional[BibliaBotConfig] = None):
        bc = biblia_cfg or load_biblia_bot_config()
        super().__init__(
            bot_token=bc.BIBLIA_BOT_TOKEN,
            database_url=bc.database_url,
        )

    def _register_features(self) -> None:
        messaging_feature = ScriptureMessagingFeature(
            user_storage=self.user_storage,
            message_copier=self.message_copier,
            feature_manager=self.feature_manager,
        )
        support_feature = SupportFeature(self.user_storage)
        referral_feature = ReferralProgramFeature(
            user_storage=self.user_storage,
            bot=self.bot,
        )
        payment_feature = DonationPaymentFeature(
            user_storage=self.user_storage,
            yookassa_service=self.yookassa_service,
            bzb_service=self.bzb_service,
            bot=self.bot,
        )
        mailing_html_agents = AgentsClient(self.user_storage)
        marathon_feature = DonationMarathonFeature(
            user_storage=self.user_storage,
            bot=self.bot,
            agents_client=mailing_html_agents,
        )
        marathon_feature.bind_payment_feature(payment_feature)
        mailing_feature = MailingFeature(
            user_storage=self.user_storage,
            bot=self.bot,
            feature_manager=self.feature_manager,
        )
        scheduled_mailing = ScheduledMailingFeature(
            user_storage=self.user_storage,
            bot=self.bot,
            openai_client=self.openai_client,
            agents_client=mailing_html_agents,
        )
        scripture_mailing = ScriptureEncouragementMailingFeature(
            user_storage=self.user_storage,
            bot=self.bot,
            openai_client=self.openai_client,
            agents_client=mailing_html_agents,
        )
        faq_feature = FrequentQuestionsFeature(
            feature_manager=self.feature_manager,
            user_storage=self.user_storage,
        )
        personal_prayer = PersonalPrayerFeature(user_storage=self.user_storage)
        scripture_challenge = ScriptureChallengeFeature(user_storage=self.user_storage)
        scripture_challenge_scheduler = ScriptureChallengeSchedulerFeature(
            user_storage=self.user_storage,
            challenge_feature=scripture_challenge,
        )
        media_id_helper = MediaIdHelperFeature(
            user_storage=self.user_storage,
            bot=self.bot,
            feature_manager=self.feature_manager,
        )
        daily_report = DailyAdminReportFeature(user_storage=self.user_storage)

        features = [
            messaging_feature,
            support_feature,
            referral_feature,
            payment_feature,
            marathon_feature,
            mailing_feature,
            scheduled_mailing,
            scripture_mailing,
            faq_feature,
            personal_prayer,
            scripture_challenge,
            scripture_challenge_scheduler,
            media_id_helper,
            daily_report,
        ]
        for feature in features:
            self.feature_manager.register(feature)
            if hasattr(feature, "set_bot"):
                feature.set_bot(self)
            if feature.name != "payment":
                feature.register_handlers(self.dp)

        logger.info("✅ Зарегистрировано фич: %s", len(features))

    def _register_handlers(self) -> None:
        AppCommandHandlers(self.dp, self.feature_manager).register_handlers()

        payment = self.feature_manager.get("payment")
        payment.register_handlers(self.dp)

        # До MessageHandlers: иначе universal message/callback перехватят /new_mailing и aml:*
        admin_mailing.register_admin_mailing_handlers(
            self.dp, self.user_storage, self.bot
        )

        message_handlers = MessageHandlers(
            self.dp,
            self.feature_manager,
            self.media_processor,
            self.message_copier,
            self.interaction_logger,
        )
        message_handlers.set_bot(self)
        message_handlers.register_handlers()
