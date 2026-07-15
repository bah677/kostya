"""Цитаты из Писания в топик дайджеста по расписанию (7/9/12/15/18/21 МСК, минута 1–15)."""

from __future__ import annotations

import logging
from typing import List, Literal, Optional

from aiogram import Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.admin_guard import is_telegram_admin
from bot.features.base import BaseFeature
from bot.services.club_scripture_pulse import (
    DEFAULT_PULSE_HOURS,
    ScripturePulseResult,
    commit_pulse_run,
    parse_pulse_hours,
    pick_random_pulse_minutes,
    run_club_scripture_pulse,
)
from bot.utils.club_digest_topic import send_html_to_club_digest_topic
from bot.utils.pulse_test_args import parse_pulse_test_args
from bot.utils.telegram_errors import format_exception
from bot.utils.telegram_send import send_telegram_html_chunks
from bot.texts.ru_club_scripture_pulse import (
    scripture_pulse_failed_text,
    scripture_pulse_sent_text,
    scripture_pulse_skipped_text,
    scripture_pulse_test_build_text,
    scripture_pulse_test_usage_text,
    scripture_pulse_where,
)
from config import config

logger = logging.getLogger(__name__)

PulseSendTarget = Literal["group", "dm"]


class ClubScripturePulseFeature(BaseFeature):
    """По слотам МСК подбирает цитату Писания под переписку с прошлого запуска."""

    name = "club_scripture_pulse"

    def __init__(self, user_storage, bot):
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._pulse_job_ids: List[str] = []

    def _pulse_hours(self) -> tuple[int, ...]:
        return parse_pulse_hours(config.CLUB_SCRIPTURE_PULSE_HOURS)

    def register_handlers(self, dp: Dispatcher) -> None:
        admin_private = F.chat.type == ChatType.PRIVATE
        dp.message.register(
            self._cmd_pulse_test,
            admin_private,
            Command("scripture_pulse_test"),
        )

    async def initialize(self) -> None:
        await super().initialize()
        if not config.club_scripture_group_active:
            logger.info("[%s] Выключено (group pulse off / outreach DM)", self.name)
            return
        if not (config.DEEPSEEK_API_KEY or "").strip():
            logger.warning("[%s] Нет DEEPSEEK_API_KEY", self.name)
            return
        if not config.CLUB_GROUP_ID or not config.CLUB_DIGEST_TOPIC_ID:
            logger.warning("[%s] Нет CLUB_GROUP_ID / CLUB_DIGEST_TOPIC_ID", self.name)
            return

        self._scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
        self._schedule_daily_pulse_jobs()
        self._scheduler.add_job(
            self._schedule_daily_pulse_jobs,
            CronTrigger(hour=0, minute=1, timezone="Europe/Moscow"),
            id="scripture_pulse_reschedule",
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info("[%s] Планировщик запущен", self.name)

    async def teardown(self) -> None:
        if self._scheduler:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
            self._scheduler = None
        self._pulse_job_ids.clear()
        logger.info("[%s] Фича остановлена", self.name)

    def _schedule_daily_pulse_jobs(self) -> None:
        if not self._scheduler:
            return
        for jid in self._pulse_job_ids:
            try:
                self._scheduler.remove_job(jid)
            except Exception:
                pass
        self._pulse_job_ids.clear()

        hours = self._pulse_hours()
        minutes = pick_random_pulse_minutes(
            minute_min=config.CLUB_SCRIPTURE_PULSE_MINUTE_MIN,
            minute_max=config.CLUB_SCRIPTURE_PULSE_MINUTE_MAX,
            pulse_hours=hours,
        )
        for hour in hours:
            minute = minutes[hour]
            jid = f"scripture_pulse_{hour}"
            self._scheduler.add_job(
                self._run_scheduled_slot,
                CronTrigger(
                    hour=hour,
                    minute=minute,
                    timezone="Europe/Moscow",
                ),
                id=jid,
                replace_existing=True,
                kwargs={"slot_hour": hour},
            )
            self._pulse_job_ids.append(jid)
            logger.info(
                "[%s] Слот %02d:%02d МСК → топик %s",
                self.name,
                hour,
                minute,
                config.CLUB_DIGEST_TOPIC_ID,
            )

    async def _run_scheduled_slot(self, *, slot_hour: int) -> None:
        await self._execute_pulse(slot_hour=slot_hour, target="group", persist_state=True)

    async def _cmd_pulse_test(
        self, message: Message, command: CommandObject
    ) -> None:
        uid = message.from_user.id if message.from_user else 0
        if not await is_telegram_admin(self.user_storage, uid):
            return

        target, slot_hour, persist_state = parse_pulse_test_args(
            command.args,
            default_slot_hour=DEFAULT_PULSE_HOURS[0],
        )
        if target is None:
            await message.answer(
                scripture_pulse_test_usage_text(),
                parse_mode=ParseMode.HTML,
            )
            return

        where = scripture_pulse_where(target=target)
        await message.answer(
            scripture_pulse_test_build_text(slot_hour=slot_hour, where=where)
        )
        result, ok = await self._execute_pulse(
            slot_hour=slot_hour,
            target=target,
            dm_user_id=uid,
            persist_state=persist_state and target == "group",
        )
        if result.skipped:
            since_s = ""
            if result.since_at:
                since_s = result.since_at.strftime("%d.%m %H:%M")
            await message.answer(
                scripture_pulse_skipped_text(
                    skip_reason=result.skip_reason,
                    message_count=result.message_count,
                    since_s=since_s,
                )
            )
            return
        if ok:
            await message.answer(
                scripture_pulse_sent_text(
                    where=where, message_count=result.message_count
                )
            )
        else:
            await message.answer(scripture_pulse_failed_text(where=where))

    async def _execute_pulse(
        self,
        *,
        slot_hour: int,
        target: PulseSendTarget,
        dm_user_id: int = 0,
        persist_state: bool = True,
    ) -> tuple[ScripturePulseResult, bool]:
        result = await run_club_scripture_pulse(
            self.user_storage.pool,
            club_group_id=config.CLUB_GROUP_ID,
            api_key=(config.DEEPSEEK_API_KEY or "").strip(),
            digest_topic_id=config.CLUB_DIGEST_TOPIC_ID,
            slot_hour=slot_hour,
            pulse_hours=self._pulse_hours(),
            min_messages=config.CLUB_SCRIPTURE_PULSE_MIN_MESSAGES,
        )
        if result.skipped:
            logger.info(
                "[%s] слот %s: пропуск — %s (msg=%s)",
                self.name,
                slot_hour,
                result.skip_reason,
                result.message_count,
            )
            return result, False

        if target == "dm":
            ok = await self._send_private(dm_user_id, result.html)
        else:
            ok = await send_html_to_club_digest_topic(
                self.bot,
                chat_id=config.CLUB_GROUP_ID,
                topic_id=int(config.CLUB_DIGEST_TOPIC_ID),
                html=result.html,
                log_prefix=self.name,
            )

        if ok:
            if persist_state and target == "group":
                commit_pulse_run(sent_html=result.html)
            logger.info(
                "[%s] слот %s → %s (msg=%s)",
                self.name,
                slot_hour,
                target,
                result.message_count,
            )
        else:
            logger.error("[%s] слот %s: не удалось отправить (%s)", self.name, slot_hour, target)
        return result, ok

    async def _send_private(self, user_id: int, html: str) -> bool:
        try:
            await send_telegram_html_chunks(self.bot, user_id, html, sanitize=False)
            return True
        except Exception as e:
            logger.error(
                "[%s] send private %s: %s", self.name, user_id, format_exception(e)
            )
            return False
