"""Cron 08:15 МСК: благодарность донорам за прошлые квотные сутки + админам."""

from __future__ import annotations

import logging
from typing import Optional

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.features.base import BaseFeature
from bot.services.donor_thanks_morning import run_donor_thanks_morning

logger = logging.getLogger(__name__)

_MSK = "Europe/Moscow"


class DonorThanksMorningFeature(BaseFeature):
    name = "donor_thanks_morning"

    def __init__(self, user_storage, bot: Optional[Bot] = None) -> None:
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot
        self.scheduler = AsyncIOScheduler()

    def set_bot(self, telegram_app) -> None:
        self.bot = telegram_app.bot if telegram_app else self.bot

    def register_handlers(self, dp) -> None:
        pass

    async def initialize(self) -> None:
        for name in (
            "apscheduler",
            "apscheduler.scheduler",
            "apscheduler.executors.default",
        ):
            logging.getLogger(name).setLevel(logging.WARNING)
        if not self.scheduler.running:
            self.scheduler.start()
        self.scheduler.add_job(
            self._run_job,
            CronTrigger(hour=8, minute=15, timezone=_MSK),
            id="donor_thanks_morning_0815",
            replace_existing=True,
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
        )
        logger.info("[%s] cron 08:15 Europe/Moscow", self.name)

    async def teardown(self) -> None:
        try:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
        except Exception as e:
            logger.warning("[%s] scheduler shutdown: %s", self.name, e)

    async def _run_job(self) -> None:
        if not self.bot:
            logger.error("[%s] нет bot — пропуск", self.name)
            return
        try:
            result = await run_donor_thanks_morning(
                self.bot,
                self.user_storage,
                force=False,
            )
            if result.skipped:
                logger.info("[%s] уже отправлено за %s", self.name, result.quota_day)
            else:
                logger.info(
                    "[%s] ok=%s fail=%s recipients=%s limit=%s",
                    self.name,
                    result.sent_ok,
                    result.sent_fail,
                    len(result.recipient_ids),
                    result.limit_slots,
                )
        except Exception as e:
            logger.error("[%s] job failed: %s", self.name, e, exc_info=True)
