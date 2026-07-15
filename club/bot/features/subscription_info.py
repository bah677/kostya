"""
Фича отображения информации о подписке пользователя.
Команда /subs показывает дату окончания подписки и кнопку продления.
"""

import logging
from datetime import datetime
from aiogram import Dispatcher
from aiogram.types import Message, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode

from bot.features.base import BaseFeature
from bot.features.club_group import SUBS_CLUB_CALLBACK_DATA
from bot.texts import ru_subscription_info as subs_txt
from bot.utils.user_ui import render_user_screen, with_main_menu
from config import config
from storage.license_types import LICENSE_TYPE_ADMIN_SUBSCRIPTION

logger = logging.getLogger(__name__)


class SubscriptionInfoFeature(BaseFeature):
    """Фича отображения информации о подписке."""

    name = "subscription_info"
    
    def __init__(self, user_storage, feature_manager):
        super().__init__()
        self.user_storage = user_storage
        self.feature_manager = feature_manager
        self.bot = None
    
    def set_bot(self, bot):
        """Устанавливает экземпляр бота."""
        self.bot = bot
    
    async def initialize(self) -> None:
        """Инициализация фичи."""
        logger.info(f"[{self.name}] Фича инициализирована")
    
    async def teardown(self) -> None:
        """Очистка при отключении фичи."""
        logger.info(f"[{self.name}] Фича остановлена")
    
    def register_handlers(self, _dp: Dispatcher) -> None:
        """Команда ``/subs`` регистрируется в ``bot/handlers/commands.py`` (без дубля)."""

    async def cmd_subs(
        self, message: Message, state: FSMContext, *, edit: bool = False
    ):
        """Показывает информацию о подписке."""
        user_id = message.from_user.id
        
        license_info = await self.user_storage.get_user_active_license(user_id)
        now = datetime.now()
        
        has_license = license_info and license_info['expires_at'] > now
        is_admin_sub = (
            has_license
            and (license_info.get("license_type") or "") == LICENSE_TYPE_ADMIN_SUBSCRIPTION
        )
        
        rows = []
        if has_license:
            expires_str = license_info['expires_at'].strftime('%d.%m.%Y')
            if is_admin_sub:
                text = subs_txt.subs_admin_subscription_html(expires_str=expires_str)
                if config.CLUB_GROUP_ID:
                    rows.append(
                        [
                            InlineKeyboardButton(
                                text=subs_txt.BTN_CLUB,
                                callback_data=SUBS_CLUB_CALLBACK_DATA,
                            )
                        ]
                    )
            else:
                text = subs_txt.subs_active_html(expires_str=expires_str)
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=subs_txt.BTN_RENEW,
                            callback_data="payment_start",
                        )
                    ]
                )
                if config.CLUB_GROUP_ID:
                    rows.append(
                        [
                            InlineKeyboardButton(
                                text=subs_txt.BTN_CLUB,
                                callback_data=SUBS_CLUB_CALLBACK_DATA,
                            )
                        ]
                    )
        else:
            text = subs_txt.subs_inactive_html()
            rows.append(
                [
                    InlineKeyboardButton(
                        text=subs_txt.BTN_BUY,
                        callback_data="payment_start",
                    )
                ]
            )

        await render_user_screen(
            message,
            text=text,
            reply_markup=with_main_menu(rows),
            edit=edit,
            add_main_menu=False,
        )
        logger.info(f"📅 Subscription info shown to user {user_id} (has_license={has_license})")
        
        await state.clear()
