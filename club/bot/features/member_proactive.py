"""Проактивные сообщения member-агента (планировщик + рассылка)."""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

from bot.features.base import BaseFeature
from bot.features.subscription_reminder import today_moscow
from bot.services.member_proactive_service import run_proactive_batch
from config import config

if TYPE_CHECKING:
    from openai import AsyncOpenAI
    from rag.runtime import RagStack

logger = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")


def _parse_proactive_hours(raw: str) -> list[int]:
    hours: list[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part.isdigit():
            continue
        h = int(part)
        if 0 <= h <= 23:
            hours.append(h)
    return hours or [9, 12, 15, 18, 21]


def _proactive_hours_cron_expr(raw: str) -> str:
    """Выражение hour для CronTrigger (строка '9,12,15', не list)."""
    return ",".join(str(h) for h in _parse_proactive_hours(raw))


class MemberProactiveFeature(BaseFeature):
    name = "member_proactive"

    def __init__(self, user_storage, bot, feature_manager=None):
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot
        self.feature_manager = feature_manager
        self.rag_stack: Optional["RagStack"] = None
        self._llm_client: Optional["AsyncOpenAI"] = None
        self._scheduler: Optional[AsyncIOScheduler] = None

    def set_rag_stack(self, rag_stack: "RagStack") -> None:
        self.rag_stack = rag_stack

    def set_llm_client(self, client: "AsyncOpenAI") -> None:
        self._llm_client = client

    async def initialize(self) -> None:
        if not config.MEMBER_PROACTIVE_ENABLED:
            logger.info("[%s] disabled (MEMBER_PROACTIVE_ENABLED)", self.name)
            return
        if not self._llm_client:
            logger.warning("[%s] no LLM client — skip scheduler", self.name)
            return
        self._scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
        proactive_hours = _parse_proactive_hours(config.MEMBER_PROACTIVE_HOURS)
        hour_expr = _proactive_hours_cron_expr(config.MEMBER_PROACTIVE_HOURS)
        self._scheduler.add_job(
            self._run_batch,
            CronTrigger(
                hour=hour_expr,
                minute=config.MEMBER_PROACTIVE_MINUTE,
                timezone="Europe/Moscow",
            ),
            id="member_proactive_batch",
        )
        self._scheduler.start()
        logger.info(
            "[%s] scheduler: hours=%s minute=%s",
            self.name,
            proactive_hours,
            config.MEMBER_PROACTIVE_MINUTE,
        )

    async def teardown(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        logger.info("[%s] stopped", self.name)

    def register_handlers(self, _dp) -> None:
        pass

    async def _run_batch(self) -> None:
        if not self._llm_client:
            return
        today = today_moscow()
        try:
            n = await run_proactive_batch(
                user_storage=self.user_storage,
                bot=self.bot,
                llm_client=self._llm_client,
                rag_stack=self.rag_stack,
                today_msk_date=today,
                max_users=config.MEMBER_PROACTIVE_MAX_PER_RUN,
            )
            logger.info("[%s] batch done sent=%s date=%s", self.name, n, today)
        except Exception as e:
            logger.error("[%s] batch failed: %s", self.name, e, exc_info=True)
