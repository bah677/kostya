"""Планировщик ежедневной выдачи отрывков и еженедельного пересмотра плана."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.features.base import BaseFeature

logger = logging.getLogger(__name__)


class ScriptureChallengeSchedulerFeature(BaseFeature):
    name = "scripture_challenge_scheduler"

    def __init__(self, user_storage, challenge_feature) -> None:
        super().__init__()
        self.user_storage = user_storage
        self.challenge_feature = challenge_feature
        self.scheduler = AsyncIOScheduler()

    def register_handlers(self, dp) -> None:
        pass

    async def initialize(self) -> None:
        for name in ("apscheduler", "apscheduler.scheduler", "apscheduler.executors.default"):
            logging.getLogger(name).setLevel(logging.WARNING)

        if self.user_storage.pool is None:
            logger.warning("[%s] Cron пропущен: пул БД не открыт", self.name)
            return

        if not self.scheduler.running:
            self.scheduler.start()

        self.scheduler.add_job(
            self._process_due_deliveries,
            CronTrigger(minute="*/10", timezone="UTC"),
            id="scripture_challenge_daily",
            replace_existing=True,
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            self._process_weekly_reviews,
            CronTrigger(minute="15,45", timezone="UTC"),
            id="scripture_challenge_weekly",
            replace_existing=True,
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
        )
        logger.info("[%s] jobs: delivery */10 min, weekly :15/:45 UTC", self.name)

    async def teardown(self) -> None:
        try:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
        except Exception as e:
            logger.warning("[%s] scheduler shutdown: %s", self.name, e)

    async def _process_due_deliveries(self) -> None:
        now = datetime.now(timezone.utc)
        due = await self.user_storage.list_challenges_due_delivery(now)
        for ch in due:
            try:
                await self.challenge_feature.send_daily_passage(ch)
            except Exception as e:
                logger.error(
                    "[%s] daily delivery challenge=%s: %s", self.name, ch.get("id"), e
                )

    async def _process_weekly_reviews(self) -> None:
        now = datetime.now(timezone.utc)
        due = await self.user_storage.list_challenges_due_weekly_review(now)
        for ch in due:
            try:
                await self.challenge_feature.run_weekly_review(ch)
            except Exception as e:
                logger.error(
                    "[%s] weekly review challenge=%s: %s", self.name, ch.get("id"), e
                )
