"""Фоновые джобы: second_prayer_nudge (час) + pending_reply (10 мин)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from aiogram import Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.features.base import BaseFeature
from bot.services.crisis_classifier import is_crisis_context
from bot.utils.admin_channel import send_admin_html_message
from config import config
from openai_client.agents_client import AgentsClient
from storage.db.second_prayer_nudge import format_nudge_text

logger = logging.getLogger(__name__)

# Глобальный флаг паузы nudge/deferred при массовом сбое генерации
_JOBS_PAUSED = False


def set_background_jobs_paused(paused: bool) -> None:
    global _JOBS_PAUSED
    _JOBS_PAUSED = bool(paused)
    logger.warning("background jobs paused=%s", _JOBS_PAUSED)


def background_jobs_paused() -> bool:
    return _JOBS_PAUSED


def _local_hour_for_offset(offset_min: int) -> datetime:
    """Сейчас в локали пользователя (timezone_offset в минутах от UTC)."""
    utc_now = datetime.now(timezone.utc)
    return utc_now + timedelta(minutes=int(offset_min or 0))


def _next_local_10am(offset_min: int) -> datetime:
    local = _local_hour_for_offset(offset_min)
    target = local.replace(hour=10, minute=0, second=0, microsecond=0)
    if local.hour >= 10 and not (9 <= local.hour < 21):
        # уже после окна или до — на ближайшие 10:00
        if local >= target:
            target = target + timedelta(days=1)
    elif local.hour >= 21:
        target = target + timedelta(days=1)
    elif local.hour < 9:
        pass  # сегодня 10:00
    else:
        # внутри окна — не должны сюда попасть
        target = target + timedelta(days=1)
    # back to UTC
    return target - timedelta(minutes=int(offset_min or 0))


class BackgroundJobsFeature(BaseFeature):
    name = "background_jobs"

    def __init__(self, user_storage, bot=None) -> None:
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot
        self.scheduler = AsyncIOScheduler()
        self.agents_client: Optional[AgentsClient] = None

    def set_bot(self, bot) -> None:
        self.bot = bot

    def register_handlers(self, dp: Dispatcher) -> None:
        pass

    async def initialize(self) -> None:
        self.agents_client = AgentsClient(self.user_storage)
        for name in (
            "apscheduler",
            "apscheduler.scheduler",
            "apscheduler.executors.default",
        ):
            logging.getLogger(name).setLevel(logging.WARNING)
        if not self.scheduler.running:
            self.scheduler.start()
        self.scheduler.add_job(
            self._run_second_prayer_nudges,
            "interval",
            hours=1,
            id="second_prayer_nudge",
            replace_existing=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            self._run_pending_replies,
            "interval",
            minutes=10,
            id="pending_reply",
            replace_existing=True,
            max_instances=1,
        )
        logger.info("[%s] scheduler started (nudge 1h, pending 10m)", self.name)

    async def teardown(self) -> None:
        try:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
        except Exception as e:
            logger.warning("[%s] scheduler shutdown: %s", self.name, e)

    async def _admin_alert(self, text: str) -> None:
        tg = self.bot.bot if self.bot else None
        if not tg:
            return
        tid = int(getattr(config, "TECH_ALERT_TOPIC_ID", 0) or 0) or None
        try:
            await send_admin_html_message(tg, text, message_thread_id=tid)
        except Exception as e:
            logger.error("[%s] admin alert failed: %s", self.name, e)

    async def _run_second_prayer_nudges(self) -> None:
        if background_jobs_paused():
            return
        rows = await self.user_storage.fetch_due_second_prayer_nudges(limit=200)
        if not rows:
            return
        tg = self.bot.bot if self.bot else None
        if not tg:
            return
        for row in rows:
            uid = int(row["user_id"])
            try:
                voice_n = await self.user_storage.count_sent_prayer_voices(uid)
                if voice_n >= 2:
                    await self.user_storage.delete_second_prayer_nudge(uid)
                    continue

                # последнее сообщение пользователя
                hist = await self.user_storage.get_private_chat_history(uid, limit=8)
                last_user = ""
                for m in reversed(hist):
                    if m.get("role") == "user" and (m.get("content") or "").strip():
                        last_user = str(m["content"])
                        break
                if last_user and await is_crisis_context(
                    self.user_storage,
                    self.agents_client,
                    uid,
                    last_user,
                    point="deferred",
                ):
                    cnt = int(row.get("crisis_postpone_count") or 0)
                    if cnt >= 2:
                        await self.user_storage.delete_second_prayer_nudge(uid)
                    else:
                        await self.user_storage.postpone_second_prayer_nudge(
                            uid, days=2, increment_crisis=True
                        )
                    continue

                offset = int(row.get("timezone_offset") or 180)
                local = _local_hour_for_offset(offset)
                if not (9 <= local.hour < 21):
                    due = _next_local_10am(offset)
                    if due.tzinfo is None:
                        due = due.replace(tzinfo=timezone.utc)
                    await self.user_storage.postpone_second_prayer_nudge(
                        uid, new_due_at=due, increment_crisis=False
                    )
                    continue

                text = format_nudge_text(row.get("topic_snippet"))
                await tg.send_message(uid, text)
                await self.user_storage.mark_second_prayer_nudge_sent(uid)
            except Exception as e:
                logger.warning("[%s] nudge uid=%s failed: %s", self.name, uid, e)

    async def _run_pending_replies(self) -> None:
        if background_jobs_paused():
            return
        stale = await self.user_storage.fail_stale_pending_replies(max_age_hours=6)
        if stale:
            await self._admin_alert(
                f"⚠️ <b>pending_reply</b>: {len(stale)} записей старше 6ч → failed"
            )

        rows = await self.user_storage.fetch_pending_replies(limit=40, max_age_hours=6)
        if not rows:
            return
        messaging = None
        try:
            if self.bot and getattr(self.bot, "feature_manager", None):
                messaging = self.bot.feature_manager.get_optional("messaging")
        except Exception:
            messaging = None
        if messaging is None or not getattr(messaging, "agents_client", None):
            return
        tg = self.bot.bot if self.bot else None
        if not tg:
            return

        for row in rows:
            rid = int(row["id"])
            uid = int(row["user_id"])
            chat_id = int(row["chat_id"])
            text = row.get("text") or ""
            try:
                reply = await messaging.agents_client.run(
                    user_message=text, user_id=uid
                )
                if not reply:
                    await self.user_storage.mark_pending_reply_attempt(
                        rid, error="empty_reply"
                    )
                    continue
                from bot.utils.telegram_html_async import (
                    normalize_llm_reply_for_telegram_async,
                )
                from bot.utils.telegram_html import strip_subscribe_cta
                from aiogram.enums import ParseMode

                body, _ = strip_subscribe_cta(reply)
                html = await normalize_llm_reply_for_telegram_async(
                    body, user_id=uid, agents_client=messaging.agents_client
                )
                await tg.send_message(chat_id, html, parse_mode=ParseMode.HTML)
                await self.user_storage.mark_pending_reply_done(rid)
            except Exception as e:
                await self.user_storage.mark_pending_reply_attempt(
                    rid, error=str(e)[:200]
                )
                logger.warning("[%s] pending_reply id=%s: %s", self.name, rid, e)
