"""Фича расписания клуба: индексация постов admins, /schedule, топик в админ-группе."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, TYPE_CHECKING

from aiogram import Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.admin_guard import is_telegram_admin
from bot.features.base import BaseFeature
from bot.filters import PRIVATE_CHAT_ONLY
from bot.handlers.messages import text_for_feature_route
from bot.media_processing import MediaProcessor
from bot.texts.prompts.media import VISION_DESCRIBE_SCHEDULE_IMAGE
from bot.services.club_schedule_service import (
    format_schedule_admin_message,
    format_schedule_topic_digest,
    index_schedule_from_group_message,
    schedule_topic_reply_html,
    try_apply_schedule_from_admin_topic,
)
from bot.texts import ru_club_schedule as sch_txt
from bot.utils.admin_channel import send_admin_html_message
from config import config

if TYPE_CHECKING:
    from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class ClubScheduleFeature(BaseFeature):
    name = "club_schedule"

    def __init__(self, user_storage, bot):
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot
        self._llm_client: Optional["AsyncOpenAI"] = None
        self._media_processor: Optional[MediaProcessor] = None
        self._scheduler: Optional[AsyncIOScheduler] = None

    def set_llm_client(self, client: "AsyncOpenAI") -> None:
        self._llm_client = client

    def set_media_processor(self, processor: MediaProcessor) -> None:
        self._media_processor = processor

    async def initialize(self) -> None:
        if config.club_schedule_topic_active:
            self._scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
            self._scheduler.add_job(
                self._post_evening_digest,
                CronTrigger(
                    hour=config.CLUB_SCHEDULE_TOPIC_DIGEST_HOUR,
                    minute=config.CLUB_SCHEDULE_TOPIC_DIGEST_MINUTE,
                    timezone="Europe/Moscow",
                ),
                id="club_schedule_topic_digest",
                replace_existing=True,
            )
            self._scheduler.start()
            logger.info(
                "[%s] topic digest: %s:%02d МСК topic=%s days=%s",
                self.name,
                config.CLUB_SCHEDULE_TOPIC_DIGEST_HOUR,
                config.CLUB_SCHEDULE_TOPIC_DIGEST_MINUTE,
                config.CLUB_SCHEDULE_ADMIN_TOPIC_ID,
                config.CLUB_SCHEDULE_TOPIC_DIGEST_DAYS,
            )
        else:
            logger.info("[%s] topic digest disabled or not configured", self.name)

    async def teardown(self) -> None:
        if self._scheduler:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
            self._scheduler = None
        logger.info("[%s] stopped", self.name)

    def register_handlers(self, dp: Dispatcher) -> None:
        dp.message.register(
            self._cmd_schedule,
            PRIVATE_CHAT_ONLY,
            Command("schedule"),
        )
        if config.CLUB_GROUP_ID:
            dp.message.register(
                self._on_club_group_text,
                F.chat.id == config.CLUB_GROUP_ID,
                F.text | F.caption,
            )

        gid = config.resolved_admin_group_id()
        topic_id = config.CLUB_SCHEDULE_ADMIN_TOPIC_ID
        if gid and topic_id > 0:
            dp.message.register(
                self._on_schedule_topic_message,
                F.chat.id == gid,
                F.message_thread_id == topic_id,
            )

    async def _cmd_schedule(self, message: Message) -> None:
        uid = message.from_user.id if message.from_user else 0
        if not await is_telegram_admin(self.user_storage, uid):
            await message.answer(sch_txt.SCHEDULE_NO_ACCESS, parse_mode=ParseMode.HTML)
            return

        args = (message.text or "").split(maxsplit=1)
        tail = args[1].strip().lower() if len(args) > 1 else ""
        if tail in ("raw", "debug"):
            mode = "raw"
        elif tail in ("today", "сегодня"):
            mode = "today"
        elif tail in ("2weeks", "14", "две"):
            body = await format_schedule_topic_digest(self.user_storage)
            await message.answer(body, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            return
        else:
            mode = "week"

        body = await format_schedule_admin_message(
            self.user_storage, mode=mode
        )
        await message.answer(body, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    async def _on_club_group_text(self, message: Message) -> None:
        if not self._llm_client:
            return
        asyncio.create_task(
            self._index_group_message_safe(message),
            name=f"club_schedule_index_{message.message_id}",
        )

    async def _on_schedule_topic_message(self, message: Message) -> None:
        if not message.from_user or message.from_user.is_bot:
            return
        if message.text and message.text.strip().startswith("/"):
            return
        asyncio.create_task(
            self._handle_topic_correction_safe(message),
            name=f"club_schedule_topic_{message.message_id}",
        )

    async def _index_group_message_safe(self, message: Message) -> None:
        try:
            result = await index_schedule_from_group_message(
                self.user_storage,
                self._llm_client,
                message,
            )
            if result and result.applied:
                logger.info(
                    "schedule indexed from group msg=%s: %s",
                    message.message_id,
                    result.summary[:200],
                )
        except Exception as e:
            logger.error(
                "schedule index group msg=%s: %s",
                message.message_id,
                e,
                exc_info=True,
            )

    async def _handle_topic_correction_safe(self, message: Message) -> None:
        try:
            await self._handle_topic_correction(message)
        except Exception as e:
            logger.error(
                "schedule topic msg=%s: %s",
                message.message_id,
                e,
                exc_info=True,
            )

    async def _resolve_topic_text(self, message: Message) -> str:
        body = (message.text or message.caption or "").strip()
        if body:
            return body
        if not self._media_processor or not message.from_user:
            logger.warning(
                "schedule topic: MediaProcessor не подключён, uid=%s",
                message.from_user.id if message.from_user else 0,
            )
            return ""
        has_media = bool(
            message.photo
            or message.document
            or message.voice
            or message.audio
            or message.video
            or message.video_note
            or message.animation
        )
        if not has_media:
            return ""
        processed = await self._media_processor.process_message(
            message,
            message.from_user.id,
            messages_row_id=None,
            vision_prompt=VISION_DESCRIBE_SCHEDULE_IMAGE,
            notify=False,
        )
        text = text_for_feature_route(processed, message)
        logger.info(
            "schedule topic vision uid=%s type=%s text_len=%s",
            message.from_user.id,
            processed.media_type.value,
            len(text),
        )
        return text

    async def _handle_topic_correction(self, message: Message) -> None:
        if not self._llm_client:
            await self.bot.send_message(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                text="⚠️ Распознавание расписания недоступно (нет LLM).",
                reply_to_message_id=message.message_id,
            )
            return

        has_media = bool(
            message.photo
            or message.document
            or message.voice
            or message.audio
            or message.video
            or message.video_note
            or message.animation
        )
        if has_media and not (message.text or message.caption or "").strip():
            await self.bot.send_message(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                text=sch_txt.SCHEDULE_TOPIC_MEDIA_PROCESSING,
                reply_to_message_id=message.message_id,
            )

        text = await self._resolve_topic_text(message)
        if not text:
            await self.bot.send_message(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                text=sch_txt.SCHEDULE_TOPIC_MEDIA_EMPTY,
                parse_mode=ParseMode.HTML,
                reply_to_message_id=message.message_id,
            )
            return

        uid = message.from_user.id
        result = await try_apply_schedule_from_admin_topic(
            self.user_storage,
            self._llm_client,
            author_id=uid,
            text=text,
            chat_id=message.chat.id,
            message_id=message.message_id,
        )
        reply = schedule_topic_reply_html(result)
        await self.bot.send_message(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text=reply,
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message.message_id,
        )
        if result and result.applied:
            logger.info(
                "schedule topic applied uid=%s msg=%s: %s",
                uid,
                message.message_id,
                (result.summary or "")[:200],
            )

    async def _post_evening_digest(self) -> None:
        if not config.club_schedule_topic_active:
            return
        try:
            body = await format_schedule_topic_digest(self.user_storage)
            ok = await send_admin_html_message(
                self.bot,
                body,
                message_thread_id=config.CLUB_SCHEDULE_ADMIN_TOPIC_ID,
                disable_preview=True,
            )
            logger.info("[%s] evening digest sent ok=%s", self.name, ok)
        except Exception as e:
            logger.error("[%s] evening digest failed: %s", self.name, e, exc_info=True)
