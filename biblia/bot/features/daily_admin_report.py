"""Ежедневный отчёт Biblia в админскую группу (00:01 Europe/Moscow) и /report в личку админам."""

from __future__ import annotations

import logging
from typing import Optional

import aiohttp
from aiogram import Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.admin_guard import is_telegram_admin
from bot.features.base import BaseFeature
from bot.services.biblia_daily_report import BibliaDailyReportCollector
from config import config

logger = logging.getLogger(__name__)

_MSK = "Europe/Moscow"


class DailyAdminReportFeature(BaseFeature):
    name = "daily_admin_report"

    def __init__(self, user_storage) -> None:
        super().__init__()
        self.user_storage = user_storage
        self.scheduler = AsyncIOScheduler()

    def register_handlers(self, dp: Dispatcher) -> None:
        dp.message.register(
            self._cmd_report,
            F.chat.type == ChatType.PRIVATE,
            Command("report"),
        )

    async def initialize(self) -> None:
        for sched_logger in (
            "apscheduler",
            "apscheduler.scheduler",
            "apscheduler.executors.default",
        ):
            logging.getLogger(sched_logger).setLevel(logging.WARNING)

        if not config.ADMIN_BOT_TOKEN or not config.ADMIN_CHANNEL_ID:
            logger.warning(
                "[%s] Cron пропущен: ADMIN_BOT_TOKEN или ADMIN_CHANNEL_ID не заданы",
                self.name,
            )
        elif self.user_storage.pool is None:
            logger.warning("[%s] Cron пропущен: пул БД не открыт", self.name)
        else:
            if not self.scheduler.running:
                self.scheduler.start()

            self.scheduler.add_job(
                self._send_scheduled_report,
                CronTrigger(hour=0, minute=1, timezone=_MSK),
                id="biblia_daily_admin_report",
                replace_existing=True,
                misfire_grace_time=3600,
                coalesce=True,
                max_instances=1,
            )
            logger.info(
                "[%s] Отчёт запланирован на 00:01 %s → thread_id=%s",
                self.name,
                _MSK,
                config.BIBLIA_REPORT_THREAD_ID or "(general)",
            )

    async def teardown(self) -> None:
        try:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
        except Exception as e:
            logger.warning("[%s] scheduler shutdown: %s", self.name, e)

    async def _cmd_report(self, message: Message) -> None:
        uid = message.from_user.id if message.from_user else None
        if uid is None or not await is_telegram_admin(self.user_storage, uid):
            await message.reply(
                "⛔ Нет доступа. Telegram ID должен быть в таблице <code>admins</code>.",
                parse_mode=ParseMode.HTML,
            )
            return

        if self.user_storage.pool is None:
            await message.reply("❌ База данных недоступна.")
            return

        wait_msg = await message.reply("⏳ Собираю отчёт…")
        try:
            report_html = await self.build_report_html(save_snapshot=False)
            await wait_msg.edit_text(
                report_html,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error("[%s] /report failed for uid=%s: %s", self.name, uid, e, exc_info=True)
            await wait_msg.edit_text("❌ Не удалось собрать отчёт. Смотрите логи бота.")

    async def build_report_html(self, *, save_snapshot: bool = True) -> str:
        if self.user_storage.pool is None:
            raise RuntimeError("DB pool is not available")
        collector = BibliaDailyReportCollector(self.user_storage.pool)
        metrics = await collector.get_all_metrics(save_snapshot=save_snapshot)
        return BibliaDailyReportCollector.format_report(metrics)

    async def _send_scheduled_report(self) -> None:
        try:
            await self.send_report()
        except Exception as e:
            logger.error("[%s] Ошибка cron-отчёта: %s", self.name, e, exc_info=True)

    async def send_report(self, *, thread_id: Optional[int] = None) -> bool:
        if not config.ADMIN_BOT_TOKEN or not config.ADMIN_CHANNEL_ID:
            logger.warning("[%s] ADMIN_BOT_TOKEN/ADMIN_CHANNEL_ID не заданы", self.name)
            return False
        if self.user_storage.pool is None:
            logger.warning("[%s] Пул БД недоступен", self.name)
            return False

        report_html = await self.build_report_html(save_snapshot=True)

        post_data = {
            "chat_id": config.ADMIN_CHANNEL_ID,
            "text": report_html,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        resolved_thread = thread_id
        if resolved_thread is None:
            resolved_thread = config.BIBLIA_REPORT_THREAD_ID
        if resolved_thread > 0:
            post_data["message_thread_id"] = resolved_thread

        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://api.telegram.org/bot{config.ADMIN_BOT_TOKEN}/sendMessage"
                async with session.post(url, json=post_data) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error("[%s] Telegram API error: %s", self.name, body)
                        return False
            logger.info(
                "[%s] Ежедневный отчёт отправлен (thread_id=%s)",
                self.name,
                resolved_thread or "(general)",
            )
            return True
        except Exception as e:
            logger.error("[%s] Ошибка отправки отчёта: %s", self.name, e, exc_info=True)
            return False
