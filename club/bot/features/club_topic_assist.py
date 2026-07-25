"""Батч-ассистент топика «общение»: cron → triage → RAG → reply (ephemeral/public)."""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from aiogram import Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from bot.features.base import BaseFeature
from bot.services.club_topic_assist_mirror import TopicAssistMirror
from bot.services.club_topic_assist_pipeline import run_topic_assist_batch
from bot.services.club_topic_assist_storage import (
    delete_reply_reservation,
    insert_reply,
)
from config import config

if TYPE_CHECKING:
    from openai import AsyncOpenAI
    from rag.runtime import RagStack

logger = logging.getLogger(__name__)


class ClubTopicAssistFeature(BaseFeature):
    name = "club_topic_assist"

    def __init__(self, user_storage, bot):
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot
        self._llm_client: Optional["AsyncOpenAI"] = None
        self._rag_stack: Optional["RagStack"] = None
        self._mirror = TopicAssistMirror(bot)
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._running = False

    def set_llm_client(self, client: "AsyncOpenAI") -> None:
        self._llm_client = client

    def set_rag_stack(self, rag_stack: "RagStack") -> None:
        self._rag_stack = rag_stack

    def register_handlers(self, dp: Dispatcher) -> None:
        # Батч-режим: онлайн-хендлеров нет, только cron в initialize.
        if config.club_topic_assist_active:
            logger.info(
                "[%s] batch mode group=%s thread=%s every=%smin "
                "lag=%s window=%s ctx_extra=%s pilot=%s mirror=%s",
                self.name,
                config.CLUB_GROUP_ID,
                config.CLUB_TOPIC_ASSIST_THREAD_ID,
                config.CLUB_TOPIC_ASSIST_BATCH_MINUTES,
                config.CLUB_TOPIC_ASSIST_LAG_MINUTES,
                config.CLUB_TOPIC_ASSIST_WINDOW_MINUTES,
                config.CLUB_TOPIC_ASSIST_CONTEXT_EXTRA_MINUTES,
                config.CLUB_TOPIC_ASSIST_PILOT_ONLY,
                self._mirror.enabled,
            )
        else:
            logger.info(
                "[%s] выкл. (ENABLED=%s thread=%s)",
                self.name,
                config.CLUB_TOPIC_ASSIST_ENABLED,
                config.CLUB_TOPIC_ASSIST_THREAD_ID,
            )

    async def initialize(self) -> None:
        await super().initialize()
        if not config.club_topic_assist_active:
            return
        if self._mirror.enabled:
            try:
                await self._mirror.ensure_topics()
            except Exception as e:
                logger.warning("[%s] mirror ensure_topics: %s", self.name, e)

        minutes = max(1, int(config.CLUB_TOPIC_ASSIST_BATCH_MINUTES or 5))
        self._scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
        self._scheduler.add_job(
            self._batch_tick,
            IntervalTrigger(minutes=minutes),
            id="club_topic_assist_batch",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self._scheduler.start()
        logger.info("[%s] scheduler every %s min", self.name, minutes)

    async def teardown(self) -> None:
        if self._scheduler:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
            self._scheduler = None

    async def _batch_tick(self) -> None:
        if self._running:
            return
        self._running = True
        try:
            await self._run_batch()
        except Exception as e:
            logger.exception("[%s] batch failed: %s", self.name, e)
            if self._mirror.enabled:
                await self._mirror.post(
                    case="error",
                    user_id=0,
                    user_name="batch",
                    context_tail="",
                    question="batch_tick",
                    answer=str(e)[:1500],
                    classify_reason="batch_exception",
                )
        finally:
            self._running = False

    async def _run_batch(self) -> None:
        api_key = (config.DEEPSEEK_API_KEY or "").strip()
        if not api_key:
            return
        answers = await run_topic_assist_batch(
            self.user_storage,
            api_key=api_key,
            llm_client=self._llm_client,
            rag_stack=self._rag_stack,
        )
        if not answers:
            return

        chat_id = int(config.CLUB_GROUP_ID)
        thread_id = int(config.CLUB_TOPIC_ASSIST_THREAD_ID)
        pool = self.user_storage.pool

        for ba in answers:
            item = ba.item
            # резервируем слот дедупа до отправки (чтобы соседний тик не дублировал)
            reserved = await insert_reply(
                pool,
                chat_id=chat_id,
                thread_id=thread_id,
                source_telegram_message_id=item.reply_to_message_id,
                user_id=item.user_id,
                visibility=item.visibility,
                question_excerpt=item.question_summary
                or f"msg:{item.reply_to_message_id}",
                answer_text=ba.answer,
                classify_reason=item.reason,
            )
            if not reserved:
                logger.info(
                    "[%s] skip duplicate source_mid=%s",
                    self.name,
                    item.reply_to_message_id,
                )
                continue

            sent = await self._send_reply(
                user_id=item.user_id,
                reply_to_message_id=item.reply_to_message_id,
                answer=ba.answer,
                visibility=item.visibility,
            )
            if not sent:
                await delete_reply_reservation(
                    pool,
                    chat_id=chat_id,
                    source_telegram_message_id=item.reply_to_message_id,
                )
                if self._mirror.enabled:
                    await self._mirror.post(
                        case="error",
                        user_id=item.user_id,
                        user_name=str(item.user_id),
                        context_tail="",
                        question=item.question_summary,
                        answer=ba.answer,
                        classify_reason=f"send_failed|{item.reason}",
                    )
                continue

            bot_mid, eph_mid = sent
            # обновим ids отправки
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE club_topic_assist_replies
                        SET bot_telegram_message_id = $1,
                            ephemeral_message_id = $2,
                            answer_text = $3
                        WHERE chat_id = $4
                          AND source_telegram_message_id = $5
                        """,
                        bot_mid,
                        eph_mid,
                        ba.answer[:4000],
                        chat_id,
                        item.reply_to_message_id,
                    )
            except Exception as e:
                logger.warning("[%s] update reply ids: %s", self.name, e)

            try:
                await self.user_storage.log_interaction(
                    user_id=item.user_id,
                    event_category="club_topic_assist",
                    event_type=f"answer_{item.visibility}",
                    data={
                        "visibility": item.visibility,
                        "rag_used": ba.rag_used,
                        "classify_reason": item.reason,
                        "thread_id": thread_id,
                        "source_message_id": item.reply_to_message_id,
                        "bot_message_id": bot_mid,
                        "ephemeral_message_id": eph_mid,
                        "mode": "batch",
                    },
                    source="club_topic_assist",
                    outcome="success",
                )
            except Exception as e:
                logger.debug("log_interaction: %s", e)

            if self._mirror.enabled:
                await self._mirror.post(
                    case=item.visibility,
                    user_id=item.user_id,
                    user_name=str(item.user_id),
                    context_tail=f"reply_to={item.reply_to_message_id}",
                    question=item.question_summary or "",
                    answer=ba.answer,
                    classify_reason=item.reason,
                    extra=f"rag={ba.rag_used} batch=1",
                )

    async def _send_reply(
        self,
        *,
        user_id: int,
        reply_to_message_id: int,
        answer: str,
        visibility: str,
    ) -> Optional[tuple]:
        """Returns (bot_message_id|None, ephemeral_message_id|None) or None on failure."""
        chat_id = int(config.CLUB_GROUP_ID)
        thread_id = int(config.CLUB_TOPIC_ASSIST_THREAD_ID)
        vis = visibility
        if vis == "public" and not config.CLUB_TOPIC_ASSIST_PUBLIC_ENABLED:
            vis = "ephemeral"
        try:
            kwargs = dict(
                chat_id=chat_id,
                text=answer,
                message_thread_id=thread_id,
                reply_to_message_id=reply_to_message_id,
            )
            if vis == "ephemeral":
                kwargs["receiver_user_id"] = user_id
            msg = await self.bot.send_message(**kwargs)
            bot_mid = getattr(msg, "message_id", None) or None
            eph_mid = getattr(msg, "ephemeral_message_id", None) or None
            # у ephemeral обычный message_id может быть 0
            if bot_mid == 0:
                bot_mid = None
            return (bot_mid, eph_mid)
        except Exception as e:
            logger.error(
                "[%s] send reply_to=%s uid=%s vis=%s: %s",
                self.name,
                reply_to_message_id,
                user_id,
                vis,
                e,
            )
            return None
