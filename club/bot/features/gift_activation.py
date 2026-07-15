"""
Фича активации подарков.
Обрабатывает ссылки вида https://t.me/bot?start=gift_CODE
"""

import html
import logging
from datetime import datetime, timedelta
from aiogram.types import Message
from aiogram.enums import ParseMode

from bot.features.base import BaseFeature
from bot.texts import ru_gift_activation as gift_txt

logger = logging.getLogger(__name__)


class GiftActivationFeature(BaseFeature):
    """Фича активации подарков"""
    
    name = "gift_activation"
    
    def __init__(self, user_storage, bot, feature_manager):
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot
        self.feature_manager = feature_manager
        self._activation_in_progress = set()
    
    async def initialize(self) -> None:
        logger.info(f"[{self.name}] Фича инициализирована")
    
    async def teardown(self) -> None:
        logger.info(f"[{self.name}] Фича остановлена")
    
    def register_handlers(self, dp) -> None:
        pass  # Обрабатывается через onboarding
    
    async def activate_gift(self, message: Message, gift_code: str) -> None:
        """Активирует подарок по коду."""
        user_id = message.from_user.id
        
        # Защита от повторной активации
        if user_id in self._activation_in_progress:
            await message.answer(gift_txt.gift_activation_in_progress)
            return
        
        self._activation_in_progress.add(user_id)
        
        try:
            # 1. Получаем подарок из БД
            gift = await self.user_storage.get_gift_by_code(gift_code)
            
            if not gift:
                await message.answer(
                    gift_txt.GIFT_NOT_FOUND_HTML,
                    parse_mode=ParseMode.HTML
                )
                return
            
            # 2. Проверяем статус
            if gift['status'] != 'active':
                await message.answer(
                    gift_txt.gift_already_used_html(
                        activated_at_str=gift['activated_at'].strftime('%d.%m.%Y'),
                    ),
                    parse_mode=ParseMode.HTML
                )
                return
            
            # 3. Проверяем срок действия
            if gift['expires_at'] < datetime.now():
                await message.answer(
                    gift_txt.gift_expired_html(
                        expires_at_str=gift['expires_at'].strftime('%d.%m.%Y'),
                    ),
                    parse_mode=ParseMode.HTML
                )
                return
            
            # 4. Запрещаем активировать свой подарок
            if gift['user_id'] == user_id:
                await message.answer(
                    gift_txt.GIFT_SELF_ACTIVATE_HTML,
                    parse_mode=ParseMode.HTML
                )
                return
            
            # 5. Получаем тариф
            tariff = await self.user_storage.get_tariff_by_id(gift['tariff_id'])
            if not tariff:
                await message.answer(
                    gift_txt.GIFT_TARIFF_MISSING_HTML,
                    parse_mode=ParseMode.HTML
                )
                return
            
            duration_days = tariff['duration_days']
            tariff_name = tariff['name']
            
            # 6. Активируем/продлеваем лицензию
            current_license = await self.user_storage.get_user_active_license(user_id)
            now = datetime.now()
            
            if current_license and current_license['expires_at'] > now:
                # Есть активная лицензия - продлеваем
                base_date = current_license['expires_at']
                new_expiry = base_date + timedelta(days=duration_days)
                logger.info(f"📅 Extending license for user {user_id} to {new_expiry}")
            else:
                base_date = now
                new_expiry = base_date + timedelta(days=duration_days)
                logger.info(f"📅 New license for user {user_id} until {new_expiry}")
            
            await self.user_storage.create_or_extend_license(
                user_id=user_id,
                order_id=gift["order_id"],
                expires_at=new_expiry,
                audit_source="gift_activation",
                audit_order_id=gift["order_id"],
            )
            
            # 7. Обновляем статус подарка
            await self.user_storage.update_gift_status(
                gift_code=gift_code,
                status='used',
                activated_by=user_id,
                activated_at=now
            )
            
            # 8. Уведомление получателю
            await message.answer(
                gift_txt.gift_activated_html(
                    tariff_name=html.escape(tariff_name),
                    expiry_str=new_expiry.strftime('%d.%m.%Y'),
                ),
                parse_mode=ParseMode.HTML
            )
            
            # 9. Уведомление дарителю
            donor_name = message.from_user.first_name or gift_txt.GIFT_DONOR_DEFAULT_NAME
            if message.from_user.last_name:
                donor_name += f" {message.from_user.last_name}"
            
            donor_message = gift_txt.gift_donor_notify_html(
                donor_name=html.escape(donor_name),
            )
            
            try:
                await self.bot.send_message(
                    gift['user_id'],
                    donor_message,
                    parse_mode=ParseMode.HTML
                )
                logger.info(f"✅ Donor {gift['user_id']} notified about activation")
            except Exception as e:
                logger.error(f"❌ Failed to notify donor: {e}")
            
            logger.info(f"✅ Gift {gift_code} activated by user {user_id}")
            
        except Exception as e:
            logger.error(f"❌ Error activating gift: {e}", exc_info=True)
            await message.answer(
                gift_txt.GIFT_ERROR_HTML,
                parse_mode=ParseMode.HTML,
            )
        finally:
            self._activation_in_progress.discard(user_id)
