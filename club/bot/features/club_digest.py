"""Ежедневный дайджест клубной группы в отдельный топик (10:00 МСК)."""

from __future__ import annotations

import logging
from typing import Optional

from aiogram import Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.admin_guard import is_telegram_admin
from bot.features.base import BaseFeature
from bot.services.club_daily_digest import build_club_daily_digest
from bot.utils.admin_channel import send_admin_html_message
from bot.utils.club_digest_topic import send_html_to_club_digest_topic
from bot.texts.ru_club_digest import (
    digest_admin_published_html,
    digest_admin_skipped_html,
    digest_test_build_text,
    digest_test_failed_text,
    digest_test_sent_text,
    digest_test_skipped_text,
    digest_test_usage_text,
)
from bot.texts.ru_targets import (
    SendTarget,
    parse_send_target_first_token,
    where_digest_topic_with_id,
    where_dm,
)
from bot.utils.telegram_errors import format_exception
from bot.utils.telegram_send import send_telegram_html_chunks
from config import config

logger = logging.getLogger(__name__)

DigestSendTarget = SendTarget
parse_digest_send_target = parse_send_target_first_token


class ClubDigestFeature(BaseFeature):
    """Публикует дайджест в топик CLUB_DIGEST_TOPIC_ID; ручной запуск — /digest_test."""

    name = "club_digest"

    def __init__(self, user_storage, bot, message_copier=None):
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot
        self.message_copier = message_copier
        self._scheduler: Optional[AsyncIOScheduler] = None

    def register_handlers(self, dp: Dispatcher) -> None:
        admin_private = F.chat.type == ChatType.PRIVATE
        for cmd in ("digest_test", "club_digest_test"):
            dp.message.register(
                self._cmd_digest_test,
                admin_private,
                Command(cmd),
            )

    async def initialize(self) -> None:
        await super().initialize()
        if not config.club_digest_group_active:
            logger.info("[%s] Выключено (group digest off / outreach DM)", self.name)
            return
        if not (config.DEEPSEEK_API_KEY or "").strip():
            logger.warning("[%s] Нет DEEPSEEK_API_KEY — планировщик не запущен", self.name)
            return
        if not config.CLUB_GROUP_ID:
            logger.warning("[%s] Нет CLUB_GROUP_ID", self.name)
            return
        if not config.CLUB_DIGEST_TOPIC_ID:
            logger.warning("[%s] Нет CLUB_DIGEST_TOPIC_ID", self.name)
            return
        self._scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
        self._scheduler.add_job(
            self._scheduled_publish,
            CronTrigger(
                hour=config.CLUB_DIGEST_HOUR,
                minute=config.CLUB_DIGEST_MINUTE,
                timezone="Europe/Moscow",
            ),
            id="club_daily_digest",
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info(
            "[%s] Планировщик: %02d:%02d МСК → группа %s, топик %s",
            self.name,
            config.CLUB_DIGEST_HOUR,
            config.CLUB_DIGEST_MINUTE,
            config.CLUB_GROUP_ID,
            config.CLUB_DIGEST_TOPIC_ID,
        )

    async def teardown(self) -> None:
        if self._scheduler:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
            self._scheduler = None
        logger.info("[%s] Фича остановлена", self.name)

    async def _cmd_digest_test(
        self, message: Message, command: CommandObject
    ) -> None:
        uid = message.from_user.id if message.from_user else 0
        if not await is_telegram_admin(self.user_storage, uid):
            return

        target = parse_digest_send_target(command.args)
        if target is None:
            await message.answer(
                digest_test_usage_text(),
                parse_mode=ParseMode.HTML,
            )
            return

        await message.answer(digest_test_build_text())
        result = await self._build()
        if result.skipped:
            await message.answer(
                digest_test_skipped_text(
                    skip_reason=result.skip_reason,
                    message_count=result.message_count,
                    participant_count=result.participant_count,
                )
            )
            return

        if target == "dm":
            ok = await self._send_private(uid, result.html)
            where = where_dm()
        else:
            ok = await self._send_to_digest_topic(result.html)
            where = where_digest_topic_with_id(int(config.CLUB_DIGEST_TOPIC_ID or 0))

        if ok:
            await message.answer(
                digest_test_sent_text(
                    where=where,
                    message_count=result.message_count,
                    participant_count=result.participant_count,
                )
            )
        else:
            await message.answer(digest_test_failed_text(where=where))

    async def _scheduled_publish(self) -> None:
        logger.info("[%s] Запуск по расписанию", self.name)
        result = await self._build()
        if result.skipped:
            logger.info(
                "[%s] Пропуск: %s (msg=%s, authors=%s)",
                self.name,
                result.skip_reason,
                result.message_count,
                result.participant_count,
            )
            await self._notify_admin_skip(result)
            return
        ok = await self._send_to_digest_topic(result.html)
        if ok:
            logger.info(
                "[%s] Дайджест опубликован (msg=%s, authors=%s)",
                self.name,
                result.message_count,
                result.participant_count,
            )
            await self._notify_admin_success(result)
        else:
            logger.error("[%s] Не удалось опубликовать дайджест", self.name)

    async def _build(self):
        pool = self.user_storage.pool
        return await build_club_daily_digest(
            pool,
            club_group_id=config.CLUB_GROUP_ID,
            api_key=(config.DEEPSEEK_API_KEY or "").strip(),
            lookback_hours=config.CLUB_DIGEST_LOOKBACK_HOURS,
            digest_topic_id=config.CLUB_DIGEST_TOPIC_ID,
            min_messages=config.CLUB_DIGEST_MIN_MESSAGES,
            min_participants=config.CLUB_DIGEST_MIN_PARTICIPANTS,
        )

    async def _send_html_chunks(
        self,
        *,
        chat_id: int,
        html: str,
        message_thread_id: Optional[int] = None,
    ) -> None:
        await send_telegram_html_chunks(
            self.bot,
            chat_id,
            html,
            message_thread_id=message_thread_id,
            sanitize=False,
        )

    async def _send_to_digest_topic(self, html: str) -> bool:
        cid = config.CLUB_GROUP_ID
        tid = int(config.CLUB_DIGEST_TOPIC_ID or 0)
        if not cid or not tid:
            logger.error("[%s] CLUB_GROUP_ID или CLUB_DIGEST_TOPIC_ID не заданы", self.name)
            return False
        return await send_html_to_club_digest_topic(
            self.bot,
            chat_id=cid,
            topic_id=tid,
            html=html,
            log_prefix=self.name,
        )

    async def _send_private(self, user_id: int, html: str) -> bool:
        try:
            await self._send_html_chunks(chat_id=user_id, html=html)
            return True
        except Exception as e:
            logger.error(
                "[%s] send private %s: %s", self.name, user_id, format_exception(e)
            )
            return False

    async def _notify_admin_success(self, result) -> None:
        if not config.ADMIN_CHANNEL_ID:
            return
        text = digest_admin_published_html(
            message_count=result.message_count,
            participant_count=result.participant_count,
        )
        await send_admin_html_message(self.bot, text)

    async def _notify_admin_skip(self, result) -> None:
        if not config.ADMIN_CHANNEL_ID:
            return
        text = digest_admin_skipped_html(
            skip_reason=result.skip_reason,
            message_count=result.message_count,
            participant_count=result.participant_count,
        )
        await send_admin_html_message(self.bot, text)
