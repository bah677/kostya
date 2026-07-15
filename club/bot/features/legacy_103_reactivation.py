"""Временный модуль: ежедневный вывод легаси 103 → stuck_dialog (10:00 МСК)."""

from __future__ import annotations

import logging
from typing import Optional

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.features.base import BaseFeature
from bot.features.followup import FollowupFeature
from config import config

logger = logging.getLogger(__name__)


class Legacy103ReactivationFeature(BaseFeature):
    name = "legacy_103_reactivation"

    def __init__(self, followup_feature: FollowupFeature, bot: Bot):
        super().__init__()
        self.followup = followup_feature
        self.bot = bot
        self.scheduler: Optional[AsyncIOScheduler] = None

    async def initialize(self) -> None:
        if not getattr(config, "LEGACY_103_REACTIVATION_ENABLED", False):
            logger.info("[%s] disabled (LEGACY_103_REACTIVATION_ENABLED)", self.name)
            return

        hour = int(getattr(config, "LEGACY_103_REACTIVATION_HOUR", 10))
        minute = int(getattr(config, "LEGACY_103_REACTIVATION_MINUTE", 0))
        self.scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
        self.scheduler.add_job(
            self._run_daily_batch,
            CronTrigger(hour=hour, minute=minute, timezone="Europe/Moscow"),
            id="legacy_103_reactivation",
        )
        self.scheduler.start()
        logger.info(
            "[%s] scheduler started at %02d:%02d MSK, batch=%s",
            self.name,
            hour,
            minute,
            getattr(config, "LEGACY_103_REACTIVATION_BATCH_SIZE", 100),
        )

    async def teardown(self) -> None:
        if self.scheduler:
            self.scheduler.shutdown()
            self.scheduler = None
        logger.info("[%s] stopped", self.name)

    def register_handlers(self, dp) -> None:
        pass

    async def _run_daily_batch(self) -> None:
        if not getattr(config, "LEGACY_103_REACTIVATION_ENABLED", False):
            return
        batch_size = int(getattr(config, "LEGACY_103_REACTIVATION_BATCH_SIZE", 100))
        try:
            stats = await self.followup.run_legacy_103_reactivation_batch(
                batch_size=batch_size
            )
            logger.info("[%s] batch finished: %s", self.name, stats)
        except Exception as e:
            logger.error("[%s] batch failed: %s", self.name, e, exc_info=True)
