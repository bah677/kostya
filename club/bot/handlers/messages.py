"""
Обработчик всех входящих сообщений с медиапроцессором.
"""

import logging
import asyncio
from typing import Optional

from aiogram import Dispatcher
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.utils.chat_action import ChatActionSender

from bot.features.base import FeatureManager
from bot.filters import PRIVATE_CHAT_ONLY, PRIVATE_INLINE_CALLBACK_ONLY
from bot.media_processing import MediaProcessor, ProcessedMedia
from bot.logging.message_copier import MessageCopier
from bot.logging.interaction_logger import InteractionLogger
from bot.states import AngelPoolStates, LegalConsentStates, MemberGiftExtensionStates, WishBoardStates
from bot.texts import ru_messaging as msg_txt
from config import config

logger = logging.getLogger(__name__)


def text_for_feature_route(processed: ProcessedMedia, message: Message) -> str:
    """Текст для маршрута в фичи: приоритет распознанному, иначе text/caption сообщения."""
    if processed.text and str(processed.text).strip():
        return str(processed.text).strip()
    if message.text:
        return message.text.strip()
    if message.caption:
        return message.caption.strip()
    return ""


# =====================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ МАРШРУТИЗАЦИИ
# =====================================================

async def route_message_to_feature(
    message: Message,
    state: FSMContext,
    processed: ProcessedMedia,
    message_id: int,
    feature_manager: FeatureManager,
    *,
    onboarding_topic_button: bool = False,
):
    """Маршрутизирует сообщение в соответствующую фичу."""
    user_id = message.from_user.id
    current_state = await state.get_state()
    
    text = text_for_feature_route(processed, message)
    
    logger.info(f"🔄 Routing message for user={user_id}, state={current_state}, text_length={len(text)}")

    if config.BOT_VARIANT == "nastya":
        if current_state and "LegalConsentStates" in current_state:
            logger.debug("Legal consent pending for user=%s, ignore message", user_id)
            return
        if current_state and "NastyaTempOnboardingStates" in current_state:
            feature = feature_manager.get("nastya_temp_onboarding")
            await feature.handle_message(message, state, text)
            return
        feature = feature_manager.get("messaging")
        await feature.handle_chat_message(
            message,
            state,
            text,
            message_id,
            onboarding_topic_button=onboarding_topic_button,
        )
        return
    
    if current_state:
        if "LegalConsentStates" in current_state:
            logger.debug("Legal consent pending for user=%s, ignore message", user_id)
            return

        current_state_lower = current_state.lower()
        
        if "onboarding" in current_state_lower:
            feature = feature_manager.get("onboarding")
            await feature.handle_message(message, state, text)

        elif "support" in current_state_lower:
            feature = feature_manager.get("support")
            await feature.handle_message(message, state, text)

        elif current_state == MemberGiftExtensionStates.waiting_recipient_query.state:
            mgift = feature_manager.get("member_gift_extension")
            if mgift:
                await mgift.handle_recipient_query(message, state, text)
            return

        elif current_state == WishBoardStates.waiting_description.state:
            wb = feature_manager.get("wish_board")
            if wb:
                await wb.handle_description(message, state, text)
            return

        elif current_state == AngelPoolStates.waiting_amount.state:
            ap = feature_manager.get("angel_pool")
            if ap:
                await ap.handle_amount(message, state, text)
            return

        elif current_state in (
            WishBoardStates.waiting_clarification.state,
            WishBoardStates.waiting_clarification_reply.state,
        ):
            wb = feature_manager.get("wish_board")
            if wb:
                await wb.handle_clarification(message, state, text)
            return

        else:
            # Неизвестное состояние - всё равно в messaging
            feature = feature_manager.get("messaging")
            await feature.handle_chat_message(
                message,
                state,
                text,
                message_id,
                onboarding_topic_button=onboarding_topic_button,
            )
    else:
        # Нет состояния - в messaging
        feature = feature_manager.get("messaging")
        await feature.handle_chat_message(
            message,
            state,
            text,
            message_id,
            onboarding_topic_button=onboarding_topic_button,
        )


# =====================================================
# ОСНОВНОЙ КЛАСС ОБРАБОТЧИКОВ
# =====================================================

def _chat_action_for_message(message: Message) -> str:
    if message.voice or message.audio:
        return "record_voice"
    if message.photo or message.video or message.video_note or message.animation:
        return "upload_photo"
    if message.document:
        return "upload_document"
    return "typing"


class MessageHandlers:
    """Обработчик всех типов сообщений"""
    
    def __init__(
        self, 
        dp: Dispatcher, 
        feature_manager: FeatureManager,
        media_processor: MediaProcessor,
        message_copier: MessageCopier,
        interaction_logger: InteractionLogger
    ):
        self.dp = dp
        self.features = feature_manager
        self.media_processor = media_processor
        self.message_copier = message_copier
        self.interaction_logger = interaction_logger
        self.bot = None
    
    def set_bot(self, bot):
        """Устанавливает ссылку на бота для доступа к очередям"""
        self.bot = bot
    
    def register_handlers(self):
        """Регистрирует все обработчики"""
        self.dp.message.register(self._universal_message_handler, PRIVATE_CHAT_ONLY)
        self.dp.callback_query.register(
            self._callback_handler, PRIVATE_INLINE_CALLBACK_ONLY
        )
        self.dp.edited_message.register(
            self._edited_message_handler, PRIVATE_CHAT_ONLY
        )
        self.dp.message_reaction.register(
            self._reaction_handler, PRIVATE_CHAT_ONLY
        )
        logger.info("✅ Message handlers registered")
    
    async def _universal_message_handler(
        self,
        message: Message,
        state: FSMContext,
        logged_message_id: Optional[int] = None,
    ):
        """
        Обрабатывает текстовые сообщения и медиа.
        Логирование уже выполнено в middleware (InboundLoggingMiddleware),
        id записи в БД пробрасывается через ``logged_message_id``.
        """
        user_id = message.from_user.id
        chat_action = _chat_action_for_message(message)
        is_media = chat_action != "typing"

        try:
            async with ChatActionSender(
                action=chat_action,
                bot=self.bot.bot if self.bot else message.bot,
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
            ):
                processed = await self.media_processor.process_message(
                    message, user_id, logged_message_id
                )
            # Если middleware успел сохранить запись — обновим в ней content
            # после распознавания. Если по какой-то причине не сохранил — тихо
            # пропустим (запись просто останется с исходным текстом из middleware).
            if logged_message_id is not None:
                if processed.text:
                    await self.message_copier.update_message_content(
                        message_id=logged_message_id,
                        content=processed.text,
                        metadata={
                            'confidence': processed.confidence,
                            'media_type': processed.media_type.value,
                            'processing_time_ms': processed.processing_time_ms,
                            **processed.metadata,
                        },
                    )
                else:
                    error_text = f"[{processed.media_type.value} (не удалось обработать)]"
                    await self.message_copier.update_message_content(
                        message_id=logged_message_id,
                        content=error_text,
                        metadata={
                            'confidence': 0.0,
                            'media_type': processed.media_type.value,
                            'processing_time_ms': processed.processing_time_ms,
                            'error': 'recognition_failed',
                            **processed.metadata,
                        },
                    )
            
            # Если нет текста - сообщаем пользователю
            #if not processed.has_text and processed.media_type != MediaType.TEXT:
                #await message.answer("📝 Пожалуйста, отправьте текстовое сообщение")
            #    return
            
            route_text = text_for_feature_route(processed, message)
            if is_media and not route_text.strip():
                await message.answer(msg_txt.MEDIA_EMPTY_HTML)
                return

            # Добавляем в очередь на обработку
            if self.bot:
                await self.bot.add_to_queue(user_id, {
                    'message': message,
                    'processed': processed,
                    'message_id': None
                })
            else:
                await route_message_to_feature(
                    message, state, processed, None, self.features
                )

        except Exception as e:
            logger.error(f"❌ Error in universal message handler: {e}", exc_info=True)
            try:
                await message.answer(msg_txt.MEDIA_HANDLER_ERROR_HTML)
            except Exception:
                pass
    
    async def _callback_handler(self, callback: CallbackQuery, state: FSMContext):
        """
        Обрабатывает callback'и.
        Логирование уже выполнено в middleware.
        """
        try:
            if callback.data.startswith('onboarding_'):
                try:
                    feature = self.features.get("onboarding")
                except KeyError:
                    logger.warning(
                        "⚠️ Onboarding callback без зарегистрированной фичи: %s",
                        callback.data,
                    )
                    await callback.answer("❌ Действие недоступно", show_alert=True)
                    return
                await feature.handle_callback(callback, state)
                return

            if callback.data and callback.data.startswith("qr:"):
                try:
                    feature = self.features.get("messaging")
                except KeyError:
                    logger.warning(
                        "⚠️ Quick reply callback без фичи messaging: %s",
                        callback.data,
                    )
                    await callback.answer(
                        "❌ Сервис временно недоступен",
                        show_alert=True,
                    )
                    return
                await feature.handle_quick_reply_callback(callback, state)
                return

            if callback.data.startswith('followup_') or callback.data.startswith(
                'self_question_'
            ):
                try:
                    feature = self.features.get("followup")
                except KeyError:
                    logger.warning(
                        "⚠️ Followup callback без фичи: %s", callback.data
                    )
                    await callback.answer(
                        "❌ Сервис временно недоступен",
                        show_alert=True,
                    )
                    return
                await feature.handle_callback(callback, state)
                return

            logger.warning(f"⚠️ Unknown callback: {callback.data}")
            await callback.answer("❌ Неизвестное действие")

        except Exception as e:
            logger.error(f"❌ Error in callback handler: {e}")
    
    async def _edited_message_handler(self, message: Message, state: FSMContext):
        """
        Обрабатывает отредактированные сообщения.
        Логирование уже выполнено в middleware.
        """
        logger.debug(f"✏️ Edited message from user {message.from_user.id}")
    
    async def _reaction_handler(self, message_reaction, state: FSMContext):
        """
        Обрабатывает реакции.
        Логирование уже выполнено в middleware.
        """
        logger.debug(f"👍 Reaction from user {message_reaction.user.id}")