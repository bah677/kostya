# bot/features/media_id_helper.py
import html
import logging

from aiogram import Dispatcher
from aiogram.enums import ParseMode
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from bot.features.base import BaseFeature
from bot.utils.admin_channel import (
    send_admin_audio,
    send_admin_document,
    send_admin_html_message,
    send_admin_photo,
    send_admin_video,
    send_admin_video_note,
    send_admin_voice,
)
from config import config

logger = logging.getLogger(__name__)


class MediaIdStates(StatesGroup):
    waiting_for_media = State()


class MediaIdHelperFeature(BaseFeature):
    """
    Получение file_id медиа по команде /code_id;
    опционально — копия в админский топик основным ботом.
    """

    name = "media_id_helper"

    def __init__(self, user_storage, bot, feature_manager=None):
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot
        self.feature_manager = feature_manager
        self.admin_channel_id = config.ADMIN_CHANNEL_ID
        self.admin_topic_id = config.MEDIA_ID_TOPIC_ID

    async def initialize(self) -> None:
        logger.info("[%s] Фича инициализирована", self.name)

    async def teardown(self) -> None:
        logger.info("[%s] Фича остановлена", self.name)

    def register_handlers(self, dp: Dispatcher) -> None:
        dp.message.register(self.cmd_code_id, Command("code_id"))
        dp.message.register(
            self._handle_media,
            StateFilter(MediaIdStates.waiting_for_media),
        )

    async def cmd_code_id(self, message: Message, state: FSMContext):
        await state.set_state(MediaIdStates.waiting_for_media)
        await message.answer(
            "<b>🖼 Получение file_id</b>\n\n"
            "Отправьте мне любой медиафайл (фото, видео, голосовое, видео-кружок, аудио, документ),\n"
            "а я верну вам его <code>file_id</code>.\n\n"
            "Этот ID можно использовать в рассылках и других местах.",
            parse_mode=ParseMode.HTML,
        )

    async def _handle_media(self, message: Message, state: FSMContext):
        file_id = None
        media_type = None
        file_name = None
        duration = None

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
            await message.answer(
                "❌ Неподдерживаемый тип файла.\n"
                "Отправьте: фото, видео, голосовое, аудио, видео-кружок или документ."
            )
            await state.clear()
            return

        response_text = (
            f"✅ <b>{media_type.upper()} получен!</b>\n\n"
            f"📎 <b>file_id:</b>\n<code>{file_id}</code>\n\n"
            f"📋 <b>Тип:</b> <code>{media_type}</code>"
        )
        if duration:
            response_text += f"\n⏱ <b>Длительность:</b> {duration} сек"
        if file_name:
            response_text += f"\n📄 <b>Имя файла:</b> <code>{file_name}</code>"

        await message.answer(response_text, parse_mode=ParseMode.HTML)
        await self._forward_to_admin(message, file_id, media_type, file_name, duration)
        await state.clear()
        logger.info(
            "📎 Media ID sent to user %s, type=%s",
            message.from_user.id if message.from_user else 0,
            media_type,
        )

    async def _forward_to_admin(
        self,
        message: Message,
        file_id: str,
        media_type: str,
        file_name: str | None = None,
        duration: int | None = None,
    ):
        try:
            if not self.admin_channel_id:
                logger.warning("⚠️ ADMIN_CHANNEL_ID not configured")
                return

            logger.info(
                "📤 Forwarding to admin: channel_id=%s, topic_id=%s",
                self.admin_channel_id,
                self.admin_topic_id,
            )

            user = message.from_user
            username_str = f"@{user.username}" if user and user.username else "нет username"
            display = user.full_name if user else ""

            caption = (
                "<b>📎 Получен file_id</b>\n\n"
                f"👤 <b>Пользователь:</b> {html.escape(display)} ({html.escape(username_str)})\n"
                f"🆔 <b>User ID:</b> <code>{user.id if user else 0}</code>\n"
                f"📋 <b>Тип:</b> <code>{html.escape(media_type)}</code>"
            )
            if duration:
                caption += f"\n⏱ <b>Длительность:</b> {duration} сек"
            if file_name:
                caption += f"\n📄 <b>Имя файла:</b> <code>{html.escape(file_name)}</code>"
            caption += f"\n\n📌 <b>file_id:</b>\n<code>{html.escape(file_id)}</code>"

            thread_kw = self.admin_topic_id if self.admin_topic_id > 0 else None
            ok = False
            if media_type == "photo":
                ok = await send_admin_photo(
                    self.bot, photo=file_id, caption=caption, thread_id=thread_kw
                )
            elif media_type == "video":
                ok = await send_admin_video(
                    self.bot, video=file_id, caption=caption, thread_id=thread_kw
                )
            elif media_type == "voice":
                ok = await send_admin_voice(
                    self.bot, voice=file_id, caption=caption, thread_id=thread_kw
                )
            elif media_type == "audio":
                ok = await send_admin_audio(
                    self.bot, audio=file_id, caption=caption, thread_id=thread_kw
                )
            elif media_type == "video_note":
                vn_ok = await send_admin_video_note(
                    self.bot, video_note=file_id, thread_id=thread_kw
                )
                cap_ok = await send_admin_html_message(
                    self.bot, caption, thread_id=thread_kw
                )
                ok = vn_ok and cap_ok
            elif media_type == "document":
                ok = await send_admin_document(
                    self.bot, document=file_id, caption=caption, thread_id=thread_kw
                )
            else:
                logger.error("Unknown media_type for admin forward: %s", media_type)
                return

            if ok:
                logger.info("✅ Media forwarded to admin topic %s", self.admin_topic_id)
            else:
                logger.error("❌ Failed to forward media to admin")
        except Exception as e:
            logger.error("❌ Error forwarding media to admin: %s", e)
