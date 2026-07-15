"""Автоматическое благословение: каждый день 10:00 MSK; случайная аудитория (~раз в 7–10 дн. на человека в среднем), HTML через LLM."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.features.base import BaseFeature
from bot.utils.mailing_llm_html_async import (
    STRICT_HTML_TAIL_FOR_PROMPT,
    ensure_llm_text_telegram_html,
)
from storage.mailing_storage import (
    CAMPAIGN_SOURCE_SCHEDULED_MAILING_DAILY,
    MailingStorage,
)
from storage.scheduled_mailing_storage import ScheduledMailingStorage

if TYPE_CHECKING:
    from openai_client.agents_client import AgentsClient
    from openai_client.assistant import OpenAIClient

logger = logging.getLogger(__name__)

_SCHEDULED_MAILING_SYSTEM_USER_ID = 0
_MSK = "Europe/Moscow"

_BLESSING_HTML_HINT = (
    "Оформи текст для Telegram HTML (parse_mode HTML): хотя бы один тег из "
    "<b>, <i>, <blockquote>. Без Markdown. Кратко, тёплый тон. "
    "Если вставляешь цитату из Писания: <blockquote>текст\\n\\n<i>(ссылка)</i></blockquote>.\n\n"
)


class ScheduledMailingFeature(BaseFeature):
    """
    Cron каждый день 10:00 Europe/Moscow. Промпт/model — из ``mailing_schedules`` (первое активное).
    Получатели каждый день — новая случайная выборка: размер подобран так, чтобы в среднем
    между попаданиями одного человека было ~7–10 дней (см. ``ScheduledMailingStorage``).
    """

    name = "scheduled_mailing"

    def __init__(
        self,
        user_storage,
        bot,
        openai_client: "OpenAIClient",
        *,
        agents_client: "AgentsClient",
    ):
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot
        self.openai_client = openai_client
        self.agents_client = agents_client
        self.storage = ScheduledMailingStorage(user_storage)
        self.scheduler = AsyncIOScheduler()
        self._active_schedules: List[Dict[str, Any]] = []

    async def initialize(self) -> None:
        for name in ("apscheduler", "apscheduler.scheduler", "apscheduler.executors.default"):
            logging.getLogger(name).setLevel(logging.WARNING)
        if not self.scheduler.running:
            self.scheduler.start()
        await self.load_schedules()
        self.scheduler.add_job(
            self.load_schedules,
            "interval",
            minutes=30,
            id="scheduled_reload_mailing_schedules",
        )
        logger.info("[%s] APScheduler запущен", self.name)

    async def teardown(self) -> None:
        try:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
        except Exception as e:
            logger.warning("scheduler shutdown: %s", e)
        logger.info("[%s] остановлена", self.name)

    def register_handlers(self, dp) -> None:
        pass

    def _ensure_automated_blessing_job(self) -> None:
        self.scheduler.add_job(
            self._blessing_mailing_tick,
            CronTrigger(hour=10, minute=0, timezone=_MSK),
            id="automated_blessing_msk10",
            replace_existing=True,
        )

    async def load_schedules(self) -> None:
        try:
            schedules = await self.storage.get_all_mailing_schedules(active_only=True)
            self._active_schedules = schedules
            for job in self.scheduler.get_jobs():
                jid = job.id or ""
                if jid.startswith("scheduled_mailing_"):
                    self.scheduler.remove_job(job.id)

            self._ensure_automated_blessing_job()
            logger.info("📅 Активных расписаний (промптов благословения): %s", len(schedules))
        except Exception as e:
            logger.error("❌ load_schedules: %s", e)

    def _pick_blessing_schedule(self) -> Dict[str, Any] | None:
        if not self._active_schedules:
            return None
        return self._active_schedules[0]

    async def _blessing_mailing_tick(self) -> None:
        try:
            now = datetime.now(timezone.utc)
            mailing_store = MailingStorage(self.user_storage.db)

            sch = self._pick_blessing_schedule()
            if not sch:
                logger.warning("⚠️ automated blessing: нет активного mailing_schedules")
                return

            prompt_base = sch["prompt"]
            model = sch.get("openai_model") or "gpt-4o-mini"
            schedule_id = sch["id"]

            async def _fetch(strict: bool) -> str | None:
                tail = STRICT_HTML_TAIL_FOR_PROMPT if strict else ""
                p = _BLESSING_HTML_HINT + prompt_base + tail
                return await self.openai_client.complete_text_prompt(
                    user_id=_SCHEDULED_MAILING_SYSTEM_USER_ID,
                    prompt=p,
                    model=model,
                    max_tokens=2048,
                    request_kind="scheduled_mailing_daily_blessing",
                )

            body = await ensure_llm_text_telegram_html(
                _fetch,
                agents_client=self.agents_client,
                log_context=f"blessing schedule#{schedule_id}",
            )
            if not body:
                logger.error(
                    "❌ automated blessing: не удалось получить Telegram HTML schedule=%s",
                    schedule_id,
                )
                return

            await self.storage.update_generated_text(schedule_id, body)

            users = await self.storage.get_random_users_for_blessing_mailing()
            if not users:
                logger.info("📭 automated blessing: нет получателей")
                return

            suffix = (
                "\n\nПоделись этим благословением с тем кто тебе дорог.\n\n"
                "Подписывайтесь"
            )
            campaign_body = f"{body}{suffix}"

            day_label = now.strftime("%Y-%m-%d")
            campaign_id = await mailing_store.create_campaign(
                {
                    "name": f"Благословение (авто) {day_label}",
                    "text": campaign_body,
                    "parse_mode": "HTML",
                    "scheduled_at": now,
                    "has_ref_link": True,
                    "buttons": [],
                    "campaign_source": CAMPAIGN_SOURCE_SCHEDULED_MAILING_DAILY,
                }
            )
            if not campaign_id:
                logger.error("❌ automated blessing: create_campaign failed")
                return

            uids = [int(u["user_id"]) for u in users]
            added = await mailing_store.add_audience_batch(campaign_id, uids)
            logger.info(
                "📨 Кампания благословения %s: аудитория=%s inserted=%s",
                campaign_id,
                len(uids),
                added,
            )
        except Exception as e:
            logger.error("❌ _blessing_mailing_tick: %s", e, exc_info=True)
