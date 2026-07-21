"""Сборка Telegram-бота: фичи и регистрация хендлеров."""

import logging
from typing import Optional

from bot.base_app import TelegramBotApp
from bot.features.group_rag_indexer import GroupRagIndexerFeature
from bot.features.yandex_disk_sync import YandexDiskSyncFeature
from bot.features.telemost_mail_sync import TelemostMailFeature
from bot.features.telemost_shorts_feature import TelemostShortsFeature
from bot.features.telemost_audio_feature import TelemostAudioFeature
from bot.features.rag_backfill_wizard import RagBackfillFeature
from bot.features.rag_source_visibility import RagSourceVisibilityFeature
from bot.features.donation_payment import DonationPaymentFeature
from bot.features.media_id_helper import MediaIdHelperFeature
from bot.features.referral_program import ReferralProgramFeature
from bot.features.scripture_messaging import ScriptureMessagingFeature
from bot.features.shorts_mail_wizard import ShortsMailWizardFeature
from bot.features.caption_editor_feature import CaptionEditorFeature
from bot.features.support import SupportFeature
from bot.handlers.messages import MessageHandlers

from command_handlers import AppCommandHandlers
from config import BibliaBotConfig, load_biblia_bot_config

logger = logging.getLogger(__name__)


class BotApplication(TelegramBotApp):
    """Диалог, поддержка, оплаты/заказы, рефералка."""

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
        media_id_helper = MediaIdHelperFeature(
            user_storage=self.user_storage,
            bot=self.bot,
            feature_manager=self.feature_manager,
        )
        rag_backfill = RagBackfillFeature()
        telemost_shorts = TelemostShortsFeature()
        telemost_audio = TelemostAudioFeature()
        shorts_mail_wizard = ShortsMailWizardFeature()
        caption_editor = CaptionEditorFeature()
        group_rag_indexer = GroupRagIndexerFeature()
        yandex_disk_sync = YandexDiskSyncFeature()
        telemost_mail = TelemostMailFeature()
        rag_source_visibility = RagSourceVisibilityFeature()

        features = [
            rag_backfill,
            telemost_shorts,
            telemost_audio,
            # До catch-all индексатора: иначе #club/#biblia в exclude-топике съедаются.
            shorts_mail_wizard,
            # Reply-редактура caption (после shorts_mail, до group indexer).
            caption_editor,
            group_rag_indexer,
            yandex_disk_sync,
            telemost_mail,
            rag_source_visibility,
            messaging_feature,
            support_feature,
            referral_feature,
            payment_feature,
            media_id_helper,
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

        message_handlers = MessageHandlers(
            self.dp,
            self.feature_manager,
            self.media_processor,
            self.message_copier,
            self.interaction_logger,
        )
        message_handlers.set_bot(self)
        message_handlers.register_handlers()
