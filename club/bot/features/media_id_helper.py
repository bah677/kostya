# bot/features/media_id_helper.py
from io import BytesIO

import html
import logging
from aiogram import Dispatcher
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from aiogram.fsm.state import State, StatesGroup

from bot.features.base import BaseFeature
from bot.utils.admin_channel import (
    send_admin_animation_bytes,
    send_admin_document_bytes,
    send_admin_html_message,
    send_admin_photo_bytes,
    send_admin_video_bytes,
    send_admin_video_note_bytes,
    send_admin_voice_bytes,
)
from bot.texts import ru_media_id_helper as mid_txt
from config import config

logger = logging.getLogger(__name__)


class MediaIdStates(StatesGroup):
    """Состояния для получения file_id"""
    waiting_for_media = State()


class MediaIdHelperFeature(BaseFeature):
    """
    Фича для получения file_id медиафайлов.
    Доступна по команде /code_id.
    """
    
    name = "media_id_helper"
    
    def __init__(self, user_storage, bot, feature_manager=None):
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot
        self.feature_manager = feature_manager
        self.admin_channel_id = config.ADMIN_CHANNEL_ID
        self.admin_topic_id = config.MEDIA_ID_TOPIC_ID  # добавить в .env
    
    async def initialize(self) -> None:
        logger.info(f"[{self.name}] Фича инициализирована")
    
    async def teardown(self) -> None:
        logger.info(f"[{self.name}] Фича остановлена")
    
    def register_handlers(self, dp: Dispatcher) -> None:
        """Регистрирует обработчики"""
        dp.message.register(self.cmd_code_id, Command("code_id"))
        dp.message.register(self._handle_media, MediaIdStates.waiting_for_media)
    
    async def cmd_code_id(self, message: Message, state: FSMContext):
        """Обработчик команды /code_id"""
        await state.set_state(MediaIdStates.waiting_for_media)
        await message.answer(
            mid_txt.CMD_CODE_ID_HTML,
            parse_mode=ParseMode.HTML,
        )
    
    async def _handle_media(self, message: Message, state: FSMContext):
        """Обрабатывает медиафайлы от пользователя"""
        file_id = None
        media_type = None
        file_name = None
        duration = None
        
        # Определяем тип медиа и получаем file_id
        if message.photo:
            file_id = message.photo[-1].file_id
            media_type = "photo"
            file_name = f"photo_{file_id[:8]}.jpg"
            
        elif message.video:
            file_id = message.video.file_id
            media_type = "video"
            duration = message.video.duration
            file_name = message.video.file_name or f"video_{file_id[:8]}.mp4"
            
        elif message.voice:
            file_id = message.voice.file_id
            media_type = "voice"
            duration = message.voice.duration
            file_name = f"voice_{file_id[:8]}.ogg"
            
        elif message.audio:
            file_id = message.audio.file_id
            media_type = "audio"
            duration = message.audio.duration
            file_name = message.audio.file_name or f"audio_{file_id[:8]}.mp3"
            
        elif message.video_note:
            file_id = message.video_note.file_id
            media_type = "video_note"
            duration = message.video_note.duration
            file_name = f"video_note_{file_id[:8]}.mp4"
            
        elif message.document:
            file_id = message.document.file_id
            media_type = "document"
            file_name = message.document.file_name or f"document_{file_id[:8]}"
            
        else:
            await message.answer(mid_txt.ERR_UNSUPPORTED_MEDIA)
            await state.clear()
            return
        
        # 1. Отправляем file_id пользователю (используем HTML вместо Markdown)
        await message.answer(
            mid_txt.user_media_received_html(
                media_type=media_type,
                file_id=file_id,
                duration=duration,
                file_name=file_name,
            ),
            parse_mode=ParseMode.HTML,
        )
        
        # 2. Отправляем копию в админский топик
        await self._forward_to_admin(message, file_id, media_type, file_name, duration)
        
        # 3. Очищаем состояние
        await state.clear()
        
        logger.info(f"📎 Media ID sent to user {message.from_user.id}, type={media_type}")
    
    async def _forward_to_admin(self, message: Message, file_id: str, media_type: str, file_name: str = None, duration: int = None):
        """Копия медиа и подпись с file_id в админский топик основным ботом."""
        try:
            if not self.admin_channel_id:
                logger.warning("⚠️ ADMIN_CHANNEL_ID not configured")
                return
            
            logger.info(f"📤 Forwarding to admin: channel_id={self.admin_channel_id}, topic_id={self.admin_topic_id}")
            
            user = message.from_user
            username_str = (
                f"@{user.username}" if user.username else mid_txt.NO_USERNAME
            )
            caption = mid_txt.admin_media_caption_html(
                user_full_name=user.full_name or "",
                username_str=username_str,
                user_id=user.id,
                media_type=media_type,
                file_id=file_id,
                duration=duration,
                file_name=file_name,
            )
            
            tg_file = await self.bot.get_file(file_id)
            buf = BytesIO()
            await self.bot.download_file(tg_file.file_path, buf)
            blob = buf.getvalue()
            thread_kw = self.admin_topic_id if self.admin_topic_id > 0 else None
            fn = file_name or f"file.{media_type}"

            ok = False
            if media_type == "photo":
                ok = await send_admin_photo_bytes(
                    self.bot, data=blob, filename=fn, caption=caption, thread_id=thread_kw
                )
            elif media_type == "video":
                ok = await send_admin_video_bytes(
                    self.bot, data=blob, filename=fn, caption=caption, thread_id=thread_kw
                )
            elif media_type == "animation":
                ok = await send_admin_animation_bytes(
                    self.bot, data=blob, filename=fn, caption=caption, thread_id=thread_kw
                )
            elif media_type == "voice":
                ok = await send_admin_voice_bytes(
                    self.bot, data=blob, filename=fn, caption=caption, thread_id=thread_kw
                )
            elif media_type == "audio":
                ok = await send_admin_document_bytes(
                    self.bot, data=blob, filename=fn, caption=caption, thread_id=thread_kw
                )
            elif media_type == "video_note":
                vn_ok = await send_admin_video_note_bytes(
                    self.bot, data=blob, filename=fn, thread_id=thread_kw
                )
                cap_ok = await send_admin_html_message(self.bot, caption, thread_id=thread_kw)
                ok = vn_ok and cap_ok
            else:
                ok = await send_admin_document_bytes(
                    self.bot, data=blob, filename=fn, caption=caption, thread_id=thread_kw
                )

            if ok:
                logger.info(f"✅ Media forwarded to admin topic {self.admin_topic_id}")
            else:
                logger.error("❌ Failed to forward media to admin topic")
                
        except Exception as e:
            logger.error(f"❌ Error forwarding media to admin: {e}")