"""Ассистент в топике «общение»: ephemeral/public ответы + RAG + пилот + зеркало."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime
from typing import Dict, Optional, TYPE_CHECKING
from zoneinfo import ZoneInfo

from aiogram import Dispatcher, F
from aiogram.enums import ChatType
from aiogram.types import Message

from bot.features.base import BaseFeature
from bot.services.club_outreach_pilot import user_in_pilot_cohort
from bot.services.club_topic_assist_context import TopicAssistContextBuffer
from bot.services.club_topic_assist_mirror import TopicAssistMirror
from bot.services.club_topic_assist_pipeline import compose_topic_answer
from config import config

if TYPE_CHECKING:
    from openai import AsyncOpenAI
    from rag.runtime import RagStack

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")


class ClubTopicAssistFeature(BaseFeature):
    name = "club_topic_assist"

    def __init__(self, user_storage, bot):
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot
        self._llm_client: Optional["AsyncOpenAI"] = None
        self._rag_stack: Optional["RagStack"] = None
        self._ctx = TopicAssistContextBuffer(
            maxlen=int(config.CLUB_TOPIC_ASSIST_CONTEXT_MSGS or 4)
        )
        self._mirror = TopicAssistMirror(bot)
        self._debounce_tasks: Dict[int, asyncio.Task] = {}
        self._pending: Dict[int, Message] = {}
        self._hourly: Dict[str, int] = defaultdict(int)

    def set_llm_client(self, client: "AsyncOpenAI") -> None:
        self._llm_client = client

    def set_rag_stack(self, rag_stack: "RagStack") -> None:
        self._rag_stack = rag_stack

    def register_handlers(self, dp: Dispatcher) -> None:
        if not config.club_topic_assist_active:
            logger.info(
                "[%s] выкл. (ENABLED=%s thread=%s)",
                self.name,
                config.CLUB_TOPIC_ASSIST_ENABLED,
                config.CLUB_TOPIC_ASSIST_THREAD_ID,
            )
            return
        gid = int(config.CLUB_GROUP_ID)
        tid = int(config.CLUB_TOPIC_ASSIST_THREAD_ID)
        dp.message.register(
            self._on_topic_message,
            F.chat.id == gid,
            F.chat.type == ChatType.SUPERGROUP,
            F.message_thread_id == tid,
            F.text,
        )
        logger.info(
            "[%s] listening group=%s thread=%s pilot_only=%s public=%s mirror=%s",
            self.name,
            gid,
            tid,
            config.CLUB_TOPIC_ASSIST_PILOT_ONLY,
            config.CLUB_TOPIC_ASSIST_PUBLIC_ENABLED,
            config.CLUB_TOPIC_ASSIST_MIRROR_ENABLED,
        )

    async def initialize(self) -> None:
        await super().initialize()
        if config.club_topic_assist_active and self._mirror.enabled:
            try:
                await self._mirror.ensure_topics()
            except Exception as e:
                logger.warning("[%s] mirror ensure_topics: %s", self.name, e)

    def _hourly_key(self, user_id: int) -> str:
        return f"{user_id}:{datetime.now(MSK).strftime('%Y%m%d%H')}"

    def _under_hourly_limit(self, user_id: int) -> bool:
        lim = int(config.CLUB_TOPIC_ASSIST_HOURLY_LIMIT or 8)
        return self._hourly[self._hourly_key(user_id)] < lim

    def _bump_hourly(self, user_id: int) -> None:
        self._hourly[self._hourly_key(user_id)] += 1

    async def _on_topic_message(self, message: Message) -> None:
        if not message.from_user or message.from_user.is_bot:
            return
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            return
        uid = int(message.from_user.id)
        self._ctx.set_maxlen(int(config.CLUB_TOPIC_ASSIST_CONTEXT_MSGS or 4))
        self._ctx.push(message.chat.id, uid, text, message.message_id)

        if config.CLUB_TOPIC_ASSIST_PILOT_ONLY:
            if not await user_in_pilot_cohort(self.user_storage, uid):
                return

        if not self._under_hourly_limit(uid):
            return

        self._pending[uid] = message
        old = self._debounce_tasks.get(uid)
        if old and not old.done():
            old.cancel()
        delay = float(config.CLUB_TOPIC_ASSIST_DEBOUNCE_SEC or 3.0)
        self._debounce_tasks[uid] = asyncio.create_task(
            self._debounced_run(uid, delay),
            name=f"cta_debounce_{uid}",
        )

    async def _debounced_run(self, user_id: int, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        message = self._pending.pop(user_id, None)
        self._debounce_tasks.pop(user_id, None)
        if message is None:
            return
        try:
            await self._process(message)
        except Exception as e:
            logger.exception("[%s] process failed uid=%s: %s", self.name, user_id, e)
            if self._mirror.enabled:
                await self._mirror.post(
                    case="error",
                    user_id=user_id,
                    user_name=message.from_user.full_name if message.from_user else "",
                    context_tail=self._ctx.format_tail(message.chat.id, user_id),
                    question=(message.text or "")[:500],
                    answer=str(e)[:1500],
                    classify_reason="exception",
                )

    async def _process(self, message: Message) -> None:
        if not message.from_user:
            return
        uid = int(message.from_user.id)
        question = (message.text or "").strip()
        context_tail = self._ctx.format_tail(message.chat.id, uid)
        api_key = (config.DEEPSEEK_API_KEY or "").strip()
        if not api_key:
            return

        result = await compose_topic_answer(
            self.user_storage,
            user_id=uid,
            question=question,
            context_tail=context_tail,
            api_key=api_key,
            llm_client=self._llm_client,
            rag_stack=self._rag_stack,
        )
        if not result.classify.intervene or not result.answer:
            return

        visibility = result.visibility
        if visibility == "public" and not config.CLUB_TOPIC_ASSIST_PUBLIC_ENABLED:
            visibility = "ephemeral"

        sent_ok = await self._send_answer(message, result.answer, visibility)
        if not sent_ok:
            return

        self._bump_hourly(uid)
        try:
            await self.user_storage.log_interaction(
                user_id=uid,
                event_category="club_topic_assist",
                event_type=f"answer_{visibility}",
                data={
                    "visibility": visibility,
                    "rag_used": result.rag_used,
                    "classify_reason": result.classify.reason,
                    "thread_id": message.message_thread_id,
                    "source_message_id": message.message_id,
                },
                source="club_topic_assist",
                outcome="success",
            )
        except Exception as e:
            logger.debug("log_interaction: %s", e)

        if self._mirror.enabled:
            await self._mirror.post(
                case=visibility,
                user_id=uid,
                user_name=message.from_user.full_name or "",
                context_tail=context_tail,
                question=question,
                answer=result.answer,
                classify_reason=result.classify.reason,
                extra=f"rag={result.rag_used}",
            )

    async def _send_answer(
        self, message: Message, answer: str, visibility: str
    ) -> bool:
        chat_id = message.chat.id
        thread_id = message.message_thread_id
        uid = message.from_user.id if message.from_user else 0
        try:
            if visibility == "ephemeral":
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=answer,
                    message_thread_id=thread_id,
                    receiver_user_id=uid,
                    reply_to_message_id=message.message_id,
                )
            else:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=answer,
                    message_thread_id=thread_id,
                    reply_to_message_id=message.message_id,
                )
            return True
        except Exception as e:
            logger.error("[%s] send failed: %s", self.name, e)
            if self._mirror.enabled:
                await self._mirror.post(
                    case="error",
                    user_id=uid,
                    user_name=message.from_user.full_name if message.from_user else "",
                    context_tail="",
                    question=(message.text or "")[:500],
                    answer=answer[:1500],
                    classify_reason=f"send_failed:{e}",
                )
            return False
