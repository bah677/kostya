"""
Композитный класс Database: собран из тематических mixin'ов (`storage/db/*.py`).

Публичная точка входа в приложении обычно — **`UserStorage`**
(`storage/user_storage.py`), наследующий этот класс.
"""

from storage.db._base import DatabaseBase
from storage.db.admin_responses import AdminResponsesMixin
from storage.db.angel_pool import AngelPoolMixin
from storage.db.admins import AdminsMixin
from storage.db.club_access import ClubAccessMixin
from storage.db.dialog_topics import DialogTopicsMixin
from storage.db.followup import FollowupMixin
from storage.db.gifts import GiftsMixin
from storage.db.legacy_reactivation import LegacyReactivationMixin
from storage.db.licenses import LicensesMixin
from storage.db.media_archive import MediaArchiveMixin
from storage.db.club_schedule import ClubScheduleMixin
from storage.db.member_profiles import MemberProfilesMixin
from storage.db.member_outreach import MemberOutreachMixin
from storage.db.messages import MessagesMixin
from storage.db.orders import OrdersMixin
from storage.db.payments import PaymentsMixin
from storage.db.attribution import AttributionMixin
from storage.db.legal_consent import LegalConsentMixin
from storage.db.promo_campaigns import PromoCampaignsMixin
from storage.db.referrals import ReferralsMixin
from storage.db.touch_key_labels import TouchKeyLabelsMixin
from storage.db.wish_board import WishBoardMixin
from storage.db.subscription_outreach import SubscriptionOutreachMixin
from storage.db.support import SupportMixin
from storage.db.tariffs import TariffsMixin
from storage.db.users import UsersMixin


class Database(
    AngelPoolMixin,
    AdminsMixin,
    UsersMixin,
    ClubAccessMixin,
    MessagesMixin,
    SupportMixin,
    PaymentsMixin,
    LicensesMixin,
    OrdersMixin,
    TariffsMixin,
    ReferralsMixin,
    TouchKeyLabelsMixin,
    WishBoardMixin,
    MemberProfilesMixin,
    MemberOutreachMixin,
    ClubScheduleMixin,
    PromoCampaignsMixin,
    LegacyReactivationMixin,
    AttributionMixin,
    LegalConsentMixin,
    SubscriptionOutreachMixin,
    FollowupMixin,
    AdminResponsesMixin,
    GiftsMixin,
    MediaArchiveMixin,
    DialogTopicsMixin,
    DatabaseBase,
):
    """Единая точка доступа к PostgreSQL.

    Каждый mixin отвечает за свою предметную область:
      - AdminsMixin           — admins (Telegram ID → право на админ-хендлеры в супергруппе)
      - UsersMixin            — таблица users (профиль, активность, бан, сессия агента)
      - ClubAccessMixin       — club_invites и club_group_member_cache
      - MessagesMixin         — messages, token_usage, interaction_logs, conversation_history
      - SupportMixin          — support_tickets
      - PaymentsMixin         — payments
      - LicensesMixin         — license (включая бонусные продления)
      - OrdersMixin           — orders (включая подарочные)
      - TariffsMixin          — tariffs + tariff_prices
      - ReferralsMixin        — referrals + ref_keys
      - TouchKeyLabelsMixin   — touch_key_labels (псевдонимы колбэков / promo)
      - MemberOutreachMixin  — member_outreach_state (лимиты проактивных DM)
      - ClubScheduleMixin     — club_schedule_events (расписание для member-агента)
      - PromoCampaignsMixin   — promo_campaigns + user_promo_assignments
      - LegacyReactivationMixin — legacy_103_reactivation (вывод 103 → stuck)
      - AttributionMixin      — attribution_touches, first/last touch
      - SubscriptionOutreachMixin — subscription_outreach_sent (идемпотентность рассылок)
      - FollowupMixin         — поля followup_step* в users
      - AdminResponsesMixin   — admin_responses (ответы админа клиенту)
      - GiftsMixin            — gifts (подарочные подписки)
      - MediaArchiveMixin     — локальный архив входящих медиа (диск + media_inbound_files)
      - DialogTopicsMixin    — dialog_topics (маппинг user_id → forum topic_id)
      - DatabaseBase          — пул подключений и get_connection()
    """
