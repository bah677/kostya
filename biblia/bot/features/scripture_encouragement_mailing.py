"""Цитата из Писания: каждый день 8:00 MSK; своя случайная аудитория (~раз в 2–3 дн. на человека в среднем), строго HTML."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.features.base import BaseFeature
from bot.utils.donation_reply import donation_club_random_meta_button
from bot.utils.mailing_llm_html_async import (
    STRICT_HTML_TAIL_FOR_PROMPT,
    ensure_llm_text_telegram_html,
)
from storage.mailing_storage import (
    CAMPAIGN_SOURCE_SCRIPTURE_ENCOURAGEMENT,
    MailingStorage,
)
from storage.scheduled_mailing_storage import ScheduledMailingStorage

if TYPE_CHECKING:
    from openai_client.agents_client import AgentsClient
    from openai_client.assistant import OpenAIClient

logger = logging.getLogger(__name__)

_SYSTEM_USER_ID = 0
_MSK = "Europe/Moscow"

_SCRIPTURE_PROMPT = (
    "На русском (Синодальный перевод или близкий литературный стиль). "
    "Выдай ОДНУ короткую ободряющую цитату из Нового Завета без проповеди: оформи как "
    "<blockquote>текст цитаты\n\n<i>(книга глава:стих)</i></blockquote> "
    "(источник только так, внутри того же blockquote). "
    "Не больше ~400 символов суммарно. Без Markdown, без приветствий."
)

_FALLBACK_QUOTES = (
    '<blockquote>Призываю вас, братия, именем Господа нашего Иисуса Христа, '
    "говорить все одно и не быть разделениям между вами\n\n"
    '<i>(1 Кор. 1:10)</i></blockquote>',
    "<blockquote>Укрепляйтесь Господом и могуществом силы Его\n\n<i>(Еф. 6:10)</i></blockquote>",
    "<blockquote>Не заботьтесь ни о чём, но всегда в молитве и прошении с благодарением "
    "открывайте свои желания пред Богом\n\n<i>(Флп. 4:6)</i></blockquote>",
)


class ScriptureEncouragementMailingFeature(BaseFeature):
    """
    Cron каждый день 8:00 Europe/Moscow. Аудитория — независимая случайная выборка;
    её размер подобран так, чтобы в среднем между цитатами для одного человека было ~2–3 дня.
    """
    name = "scripture_encouragement_mailing"

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

    async def initialize(self) -> None:
        for name in ("apscheduler", "apscheduler.scheduler", "apscheduler.executors.default"):
            logging.getLogger(name).setLevel(logging.WARNING)
        if not self.scheduler.running:
            self.scheduler.start()

        self.scheduler.add_job(
            self._enqueue_scripture_campaign,
            CronTrigger(hour=8, minute=0, timezone=_MSK),
            id="automated_scripture_msk08",
            replace_existing=True,
        )
        logger.info("[%s] cron 08:00 Europe/Moscow", self.name)

    async def teardown(self) -> None:
        try:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
        except Exception as e:
            logger.warning("scripture scheduler shutdown: %s", e)
        logger.info("[%s] остановлена", self.name)

    def register_handlers(self, dp) -> None:
        pass

    async def _enqueue_scripture_campaign(self) -> None:
        try:
            now = datetime.now(timezone.utc)
            mailing_store = MailingStorage(self.user_storage.db)

            users = await self.storage.get_random_users_for_scripture_mailing()
            if not users:
                logger.info("📭 scripture_encouragement: нет получателей")
                return

            async def _fetch(strict: bool) -> str | None:
                base = _SCRIPTURE_PROMPT + (STRICT_HTML_TAIL_FOR_PROMPT if strict else "")
                return await self.openai_client.complete_text_prompt(
                    user_id=_SYSTEM_USER_ID,
                    prompt=base,
                    model="gpt-4o-mini",
                    max_tokens=512,
                    request_kind="scripture_encouragement_mailing",
                )

            body = await ensure_llm_text_telegram_html(
                _fetch,
                agents_client=self.agents_client,
                log_context="scripture_encouragement",
            )
            if not body:
                idx = now.timetuple().tm_yday % len(_FALLBACK_QUOTES)
                body = _FALLBACK_QUOTES[idx]
                logger.warning("scripture_encouragement: HTML не получен — fallback #%s", idx)

            suffix = (
                "\n\nНе угадывай, что происходит в жизни, а спроси. "
                "В Библии точно есть ответ на твою ситуацию, просто расскажи об этом, и я найду его."
            )
            campaign_body = f"{body}{suffix}"

            day_label = now.strftime("%Y-%m-%d %H:%M UTC")
            campaign_id = await mailing_store.create_campaign(
                {
                    "name": f"Слово из Писания (авто) {day_label}",
                    "text": campaign_body,
                    "parse_mode": "HTML",
                    "scheduled_at": now,
                    "has_ref_link": False,
                    "buttons": [donation_club_random_meta_button()],
                    "campaign_source": CAMPAIGN_SOURCE_SCRIPTURE_ENCOURAGEMENT,
                }
            )
            if not campaign_id:
                logger.error("❌ scripture_encouragement: create_campaign failed")
                return

            uids = [int(u["user_id"]) for u in users]
            added = await mailing_store.add_audience_batch(campaign_id, uids)
            logger.info(
                "📖 Кампания %s «цитата»: получателей=%s, audience=%s",
                campaign_id,
                len(uids),
                added,
            )
        except Exception as e:
            logger.error("❌ scripture_encouragement: %s", e, exc_info=True)
