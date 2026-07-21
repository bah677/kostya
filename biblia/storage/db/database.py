"""
Композитный класс Database: собран из тематических mixin'ов (`storage/db/*.py`).

Публичная точка входа в приложении обычно — **`UserStorage`**
(`storage/user_storage.py`), наследующий этот класс.
"""

from storage.db._base import DatabaseBase
from storage.db.admin_responses import AdminResponsesMixin
from storage.db.admins import AdminsMixin
from storage.db.bot_content import BotContentMixin
from storage.db.club_access import ClubAccessMixin
from storage.db.donation_marathons import DonationMarathonsMixin
from storage.db.donation_subscriptions import DonationSubscriptionsMixin
from storage.db.followup import FollowupMixin
from storage.db.gifts import GiftsMixin
from storage.db.licenses import LicensesMixin
from storage.db.media_archive import MediaArchiveMixin
from storage.db.messages import MessagesMixin
from storage.db.orders import OrdersMixin
from storage.db.payments import PaymentsMixin
from storage.db.referrals import ReferralsMixin
from storage.db.scripture_challenge import ScriptureChallengeMixin
from storage.db.subscription_outreach import SubscriptionOutreachMixin
from storage.db.support import SupportMixin
from storage.db.tariffs import TariffsMixin
from storage.db.users import UsersMixin


class Database(
    UsersMixin,
    ClubAccessMixin,
    MessagesMixin,
    SupportMixin,
    PaymentsMixin,
    DonationSubscriptionsMixin,
    DonationMarathonsMixin,
    ScriptureChallengeMixin,
    LicensesMixin,
    OrdersMixin,
    TariffsMixin,
    ReferralsMixin,
    SubscriptionOutreachMixin,
    FollowupMixin,
    AdminResponsesMixin,
    AdminsMixin,
    BotContentMixin,
    GiftsMixin,
    MediaArchiveMixin,
    DatabaseBase,
):
    """Единая точка доступа к PostgreSQL.

    Каждый mixin отвечает за свою предметную область:
      - UsersMixin            — таблица users (профиль, активность, бан, сессия агента)
      - ClubAccessMixin       — club_invites и club_group_member_cache
      - MessagesMixin         — messages, token_usage, interaction_logs, conversation_history
      - SupportMixin          — support_tickets
      - PaymentsMixin         — payments
      - DonationSubscriptionsMixin — donation_subscriptions (рекуррентные донаты BZB)
      - DonationMarathonsMixin — donation_marathons + contributions
      - LicensesMixin         — license (включая бонусные продления)
      - OrdersMixin           — orders (включая подарочные)
      - TariffsMixin          — tariffs + tariff_prices
      - ReferralsMixin        — referrals + ref_keys
      - SubscriptionOutreachMixin — subscription_outreach_sent (идемпотентность рассылок)
      - FollowupMixin         — поля followup_step* в users
      - AdminResponsesMixin   — admin_responses (ответы админа клиенту)
      - AdminsMixin           — admins (доступ к /new_mailing и др.)
      - BotContentMixin       — bot_content (/more кнопки)
      - GiftsMixin            — gifts (подарочные подписки)
      - MediaArchiveMixin     — локальный архив входящих медиа (диск + media_inbound_files)
      - DatabaseBase          — пул подключений и get_connection()
    """
