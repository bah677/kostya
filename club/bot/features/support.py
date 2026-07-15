# bot/features/support.py
import asyncio
import html
import logging
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.features.base import BaseFeature
from bot.texts import ru_support as support_txt
from bot.utils.admin_channel import (
    send_admin_animation_bytes,
    send_admin_document_bytes,
    send_admin_html_message,
    send_admin_photo_bytes,
    send_admin_video_bytes,
    send_admin_video_note_bytes,
    send_admin_voice_bytes,
)
from bot.filters import PRIVATE_CHAT_ONLY
from bot.states import SupportStates
from bot.utils.user_ui import render_user_screen
from config import config

logger = logging.getLogger(__name__)

_TICKET_MEDIA_CAPTION_MAX = 900


class SupportFeature(BaseFeature):
    """Поддержка, обратная связь и доставка ответов админки пользователям."""

    @property
    def name(self) -> str:
        return "support"

    def __init__(self, user_storage):
        super().__init__()
        self.user_storage = user_storage
        self._current_mode: Dict[int, str] = {}
        self._bot: Optional[Bot] = None
        self.check_interval = 60
        self._monitor_running = False
        self._monitor_task: Optional[asyncio.Task] = None

    def set_bot(self, telegram_app):
        """Основной бот нужен для скачивания вложений и ответов пользователям."""
        self._bot = telegram_app.bot if telegram_app else None

    async def initialize(self) -> None:
        await super().initialize()
        self._monitor_running = True
        self._monitor_task = asyncio.create_task(self._answered_tickets_loop())
        logger.info(
            f"[{self.name}] Мониторинг ответов поддержки запущен (interval={self.check_interval}s)"
        )

    async def teardown(self) -> None:
        """Останавливает фоновый цикл мониторинга тикетов."""
        self._monitor_running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
        logger.info(f"[{self.name}] Мониторинг ответов поддержки остановлен")

    def register_handlers(self, dp: Dispatcher) -> None:
        dp.message.register(self.start_feedback, PRIVATE_CHAT_ONLY, Command("feedback"))

    async def start_support(
        self, message: Message, state: FSMContext, *, edit: bool = False
    ) -> None:
        user_id = message.from_user.id
        self._current_mode[user_id] = "support"
        await state.set_state(SupportStates.waiting_for_message)

        await render_user_screen(
            message,
            text=support_txt.SUPPORT_START_HTML,
            edit=edit,
        )

        logger.info(f"✅ Support started for user_id={user_id}")

    async def start_feedback(
        self, message: Message, state: FSMContext, *, edit: bool = False
    ) -> None:
        user_id = message.from_user.id
        self._current_mode[user_id] = "feedback"
        await state.set_state(SupportStates.waiting_for_message)

        await render_user_screen(
            message,
            text=support_txt.FEEDBACK_START_HTML,
            edit=edit,
        )

        logger.info(f"✅ Feedback started for user_id={user_id}")

    async def handle_message(self, message: Message, state: FSMContext, text: str) -> None:
        user_id = message.from_user.id
        content = text.strip()
        mode = self._current_mode.get(user_id, "support")

        try:
            if not content:
                await message.answer(support_txt.MSG_EMPTY)
                return

            if mode == "feedback":
                topic = support_txt.TOPIC_FEEDBACK
            else:
                topic = support_txt.TOPIC_SUPPORT

            ticket_number = await self.user_storage.create_support_ticket(
                user_id=user_id,
                topic=topic,
                message=content,
            )

            if not ticket_number:
                logger.error(f"❌ Failed to create ticket for user_id={user_id}")
                await message.answer(support_txt.MSG_CREATE_FAILED)
                await state.clear()
                self._current_mode.pop(user_id, None)
                return

            created_time = datetime.now().strftime("%d.%m.%Y %H:%M")

            if mode == "feedback":
                success_message = support_txt.MSG_FEEDBACK_THANKS
            else:
                success_message = support_txt.ticket_created_html(
                    ticket_number=ticket_number,
                    created_time=created_time,
                    content=content,
                )

            await message.answer(success_message, parse_mode=ParseMode.HTML)

            await self._notify_admins_about_ticket(
                ticket_number=ticket_number,
                user_id=user_id,
                message_text=content,
                user=message.from_user,
                is_feedback=(mode == "feedback"),
            )
            await self._forward_ticket_media_to_admin(
                message=message,
                ticket_number=ticket_number,
                user_id=user_id,
                user=message.from_user,
                is_feedback=(mode == "feedback"),
            )

            await state.clear()
            self._current_mode.pop(user_id, None)

            logger.info(f"✅ Ticket {ticket_number} created for user_id={user_id} (mode={mode})")

        except Exception as e:
            logger.error(f"❌ Failed to create ticket for user_id={user_id}: {e}", exc_info=True)
            await message.answer(support_txt.MSG_ERROR_GENERIC)
            await state.clear()
            self._current_mode.pop(user_id, None)

    async def send_admin_ticket_notification(
        self,
        *,
        ticket_number: str,
        user_id: int,
        message_text: str,
        user,
        is_feedback: bool = True,
    ) -> None:
        """Уведомление админ-канала о тикете (из support, mailing-кнопок, напоминаний подписки и т. п.)."""
        if not self._bot:
            logger.warning("send_admin_ticket_notification: bot не задан")
            return
        await self._notify_admins_about_ticket(
            ticket_number=ticket_number,
            user_id=user_id,
            message_text=message_text,
            user=user,
            is_feedback=is_feedback,
        )

    async def _notify_admins_about_ticket(
        self,
        ticket_number: str,
        user_id: int,
        message_text: str,
        user,
        is_feedback: bool = False,
    ) -> None:
        if not config.ADMIN_CHANNEL_ID:
            logger.warning("⚠️ Admin channel not configured, skipping notification")
            return

        user_name = user.first_name or ""
        if user.last_name:
            user_name += f" {user.last_name}"
        username_str = f"@{user.username}" if user.username else support_txt.NO_USERNAME

        if is_feedback:
            title = support_txt.ADMIN_TITLE_FEEDBACK
        else:
            title = support_txt.ADMIN_TITLE_SUPPORT

        esc_msg = html.escape(message_text[:500]) + ("..." if len(message_text) > 500 else "")
        notification_text = support_txt.admin_ticket_notification_html(
            title=title,
            ticket_number=ticket_number,
            user_name=user_name,
            username_str=username_str,
            user_id=user_id,
            esc_msg=esc_msg,
            created_time=datetime.now().strftime("%d.%m.%Y %H:%M"),
        )

        try:
            thread_kw = (
                config.SUPPORT_THREAD_ID
                if config.SUPPORT_THREAD_ID and config.SUPPORT_THREAD_ID > 0
                else None
            )
            ok = await send_admin_html_message(
                self._bot,
                notification_text,
                thread_id=thread_kw,
            )
            if ok:
                logger.info(
                    f"✅ Admin notification sent for ticket {ticket_number} (feedback={is_feedback})"
                )
            else:
                logger.error(f"❌ Failed to send admin notification for ticket {ticket_number}")
        except Exception as e:
            logger.error(f"❌ Error notifying admins about ticket {ticket_number}: {e}", exc_info=True)

    def _pick_ticket_media(self, message: Message) -> Optional[Tuple[str, str, str, str]]:
        """
        Telegram method name (sendPhoto …), multipart field name, file_id, filename for upload.
        """
        if message.photo:
            p = message.photo[-1]
            return ("sendPhoto", "photo", p.file_id, "photo.jpg")
        if message.video:
            v = message.video
            fn = v.file_name or "video.mp4"
            return ("sendVideo", "video", v.file_id, fn)
        if message.animation:
            a = message.animation
            fn = a.file_name or "animation.mp4"
            return ("sendAnimation", "animation", a.file_id, fn)
        if message.document:
            d = message.document
            fn = d.file_name or "document.bin"
            return ("sendDocument", "document", d.file_id, fn)
        if message.audio:
            a = message.audio
            fn = a.file_name or "audio.mp3"
            return ("sendAudio", "audio", a.file_id, fn)
        if message.voice:
            v = message.voice
            return ("sendVoice", "voice", v.file_id, "voice.ogg")
        if message.video_note:
            vn = message.video_note
            return ("sendVideoNote", "video_note", vn.file_id, "video_note.mp4")
        if message.sticker:
            st = message.sticker
            if st.is_video:
                ext = "webm"
            elif st.is_animated:
                ext = "tgs"
            else:
                ext = "webp"
            return ("sendDocument", "document", st.file_id, f"sticker.{ext}")
        return None

    def _ticket_media_caption_html(
        self,
        ticket_number: str,
        user_id: int,
        user,
        is_feedback: bool,
    ) -> str:
        username_str = f"@{user.username}" if user.username else support_txt.NO_USERNAME
        title = (
            support_txt.MEDIA_TITLE_FEEDBACK
            if is_feedback
            else support_txt.MEDIA_TITLE_SUPPORT
        )
        caption = support_txt.ticket_media_caption_html(
            title=title,
            ticket_number=ticket_number,
            user_full_name=user.full_name,
            username_str=username_str,
            user_id=user_id,
        )
        if len(caption) > _TICKET_MEDIA_CAPTION_MAX:
            caption = caption[: _TICKET_MEDIA_CAPTION_MAX - 3] + "..."
        return caption

    async def _forward_ticket_media_to_admin(
        self,
        message: Message,
        ticket_number: str,
        user_id: int,
        user,
        is_feedback: bool,
    ) -> None:
        if not config.ADMIN_CHANNEL_ID:
            return
        if not self._bot:
            logger.warning("⚠️ Bot not configured on SupportFeature, skip media relay")
            return

        picked = self._pick_ticket_media(message)
        if not picked:
            return

        _api_method, media_type, file_id, filename = picked
        try:
            tg_file = await self._bot.get_file(file_id)
            buf = BytesIO()
            await self._bot.download_file(tg_file.file_path, buf)
            blob = buf.getvalue()
            if not blob:
                logger.warning(f"⚠️ Empty media download for ticket {ticket_number}")
                return

            caption = self._ticket_media_caption_html(ticket_number, user_id, user, is_feedback)
            thread_kw = (
                config.SUPPORT_THREAD_ID
                if config.SUPPORT_THREAD_ID and config.SUPPORT_THREAD_ID > 0
                else None
            )
            if media_type == "photo":
                ok = await send_admin_photo_bytes(
                    self._bot,
                    data=blob,
                    filename=filename or "photo.jpg",
                    caption=caption,
                    thread_id=thread_kw,
                )
            elif media_type == "video":
                ok = await send_admin_video_bytes(
                    self._bot,
                    data=blob,
                    filename=filename or "video.mp4",
                    caption=caption,
                    thread_id=thread_kw,
                )
            elif media_type == "animation":
                ok = await send_admin_animation_bytes(
                    self._bot,
                    data=blob,
                    filename=filename or "animation.mp4",
                    caption=caption,
                    thread_id=thread_kw,
                )
            elif media_type == "voice":
                ok = await send_admin_voice_bytes(
                    self._bot,
                    data=blob,
                    filename=filename or "voice.ogg",
                    caption=caption,
                    thread_id=thread_kw,
                )
            elif media_type == "audio":
                ok = await send_admin_document_bytes(
                    self._bot,
                    data=blob,
                    filename=filename or "audio.mp3",
                    caption=caption,
                    thread_id=thread_kw,
                )
            elif media_type == "video_note":
                vn_ok = await send_admin_video_note_bytes(
                    self._bot,
                    data=blob,
                    filename=filename or "video_note.mp4",
                    thread_id=thread_kw,
                )
                cap_ok = await send_admin_html_message(
                    self._bot, caption, thread_id=thread_kw
                )
                ok = vn_ok and cap_ok
            else:
                ok = await send_admin_document_bytes(
                    self._bot,
                    data=blob,
                    filename=filename or "document.bin",
                    caption=caption,
                    thread_id=thread_kw,
                )
            if ok:
                logger.info(f"✅ Admin media forwarded for ticket {ticket_number}")
            else:
                logger.error(f"❌ Failed to forward ticket media for ticket {ticket_number}")
        except Exception as e:
            logger.error(
                f"❌ Error forwarding ticket media ({ticket_number}): {e}",
                exc_info=True,
            )

    async def _answered_tickets_loop(self) -> None:
        while self._monitor_running:
            try:
                await self._check_answered_tickets_once()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Error in support answered-tickets loop: {e}")
                await asyncio.sleep(self.check_interval)

    async def _check_answered_tickets_once(self) -> int:
        try:
            tickets = await self._get_answered_tickets()
            if not tickets:
                return 0

            logger.info(f"📬 Found {len(tickets)} answered tickets to process")
            processed_count = 0
            for ticket in tickets:
                try:
                    if await self._process_answered_ticket(ticket):
                        processed_count += 1
                except Exception as e:
                    logger.error(f"❌ Error processing ticket {ticket['ticket_number']}: {e}")
                    await self._update_ticket_status(ticket_id=ticket["id"], new_status="delivery_failed")

            if processed_count > 0:
                logger.info(f"✅ Successfully processed {processed_count} tickets")
            return processed_count
        except Exception as e:
            logger.error(f"❌ Critical error in support ticket monitor: {e}", exc_info=True)
            return 0

    async def _process_answered_ticket(self, ticket: Dict[str, Any]) -> bool:
        message_text = self._format_response_message(ticket)
        sent = await self._send_support_reply_to_user(
            user_id=ticket["user_id"],
            text=message_text,
        )
        if sent:
            await self._update_ticket_status(ticket_id=ticket["id"], new_status="closed")
            logger.info(
                f"✅ Ticket {ticket['ticket_number']} closed, response sent to user {ticket['user_id']}"
            )
            return True

        await self._update_ticket_status(ticket_id=ticket["id"], new_status="delivery_failed")
        logger.warning(
            f"⚠️ Ticket {ticket['ticket_number']} marked as delivery_failed for user {ticket['user_id']}"
        )
        return False

    def _format_response_message(self, ticket: Dict[str, Any]) -> str:
        return support_txt.support_ticket_reply_html(
            ticket_number=str(ticket["ticket_number"]),
            admin_response=str(ticket.get("admin_response") or ""),
        )

    async def _send_support_reply_to_user(self, user_id: int, text: str) -> bool:
        if not self._bot:
            logger.error("❌ Cannot send support reply: bot not set")
            return False
        try:
            await self._bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )
            return True
        except Exception as e:
            error_str = str(e).lower()
            if any(err in error_str for err in ("blocked", "deactivated", "chat not found")):
                logger.warning(f"👤 User {user_id} has blocked the bot or deleted account")
            else:
                logger.error(f"❌ Failed to send support message to user {user_id}: {e}")
            return False

    async def _get_answered_tickets(self) -> List[Dict[str, Any]]:
        try:
            async with self.user_storage.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT 
                        ticket_id AS id,
                        ticket_number,
                        user_id,
                        admin_response,
                        topic,
                        user_message,
                        created_at,
                        updated_at
                    FROM support_tickets 
                    WHERE status = 'answered' 
                      AND admin_response IS NOT NULL
                      AND admin_response != ''
                    ORDER BY updated_at ASC
                    """
                )
                return [dict(row) for row in rows]
        except Exception as e:
            from storage.log_util import log_storage_failure

            log_storage_failure(logger, "❌ Failed to get answered tickets", e)
            return []

    async def _update_ticket_status(self, ticket_id: int, new_status: str) -> bool:
        try:
            async with self.user_storage.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE support_tickets 
                       SET status = $1,
                           updated_at = NOW()
                     WHERE ticket_id = $2
                    """,
                    new_status,
                    ticket_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update ticket {ticket_id} status: {e}")
            return False
