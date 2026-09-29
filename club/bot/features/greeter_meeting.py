"""Закрытая встреча встречающих: кнопки RSVP + напоминания."""

from __future__ import annotations

import logging
from typing import Optional
from zoneinfo import ZoneInfo

from aiogram import Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from bot.admin_guard import is_telegram_admin
from bot.features.base import BaseFeature
from bot.services import greeter_meeting_service as gm
from bot.texts import ru_greeter_meeting as txt

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")


class GreeterMeetingFeature(BaseFeature):
    name = "greeter_meeting"

    def __init__(self, user_storage, bot, message_copier=None):
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot
        self.message_copier = message_copier
        self._scheduler: Optional[AsyncIOScheduler] = None

    def register_handlers(self, dp: Dispatcher) -> None:
        admin_private = F.chat.type == ChatType.PRIVATE
        dp.message.register(self._cmd_rsvp, admin_private, Command("gm_rsvp"))
        dp.callback_query.register(
            self._cb_coming, F.data == gm.CB_COMING
        )
        dp.callback_query.register(
            self._cb_cant, F.data == gm.CB_CANT
        )

    async def initialize(self) -> None:
        from datetime import datetime

        now = datetime.now(MSK)
        if now >= gm.MEETING_AT:
            logger.info("[%s] meeting already passed — scheduler skip", self.name)
            return

        self._scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
        if now < gm.MORNING_AT:
            self._scheduler.add_job(
                self._run_morning,
                DateTrigger(run_date=gm.MORNING_AT),
                id="greeter_meeting_morning",
                replace_existing=True,
                misfire_grace_time=3600,
            )
        if now < gm.PRE_MEETING_AT:
            self._scheduler.add_job(
                self._run_pre,
                DateTrigger(run_date=gm.PRE_MEETING_AT),
                id="greeter_meeting_pre",
                replace_existing=True,
                misfire_grace_time=900,
            )
        self._scheduler.start()
        logger.info(
            "[%s] scheduler started (morning=%s pre=%s)",
            self.name,
            gm.MORNING_AT.isoformat(),
            gm.PRE_MEETING_AT.isoformat(),
        )

    async def teardown(self) -> None:
        if self._scheduler:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
            self._scheduler = None

    async def _run_morning(self) -> None:
        logger.info("[%s] morning reminders start", self.name)
        stats = await gm.blast_wave(self.bot, self.user_storage, kind="morning")
        logger.info("[%s] morning done %s", self.name, stats)

    async def _run_pre(self) -> None:
        logger.info("[%s] pre-meeting reminders start", self.name)
        stats = await gm.blast_wave(self.bot, self.user_storage, kind="pre")
        logger.info("[%s] pre done %s", self.name, stats)

    async def _cmd_rsvp(self, message: Message) -> None:
        uid = message.from_user.id if message.from_user else 0
        if not await is_telegram_admin(self.user_storage, uid):
            return
        report = await gm.format_rsvp_report_html(self.user_storage)
        await message.answer(report, parse_mode=ParseMode.HTML)

    async def _cb_coming(self, callback: CallbackQuery) -> None:
        await self._handle_rsvp(callback, response="coming")

    async def _cb_cant(self, callback: CallbackQuery) -> None:
        await self._handle_rsvp(callback, response="cant")

    async def _handle_rsvp(self, callback: CallbackQuery, *, response: str) -> None:
        user = callback.from_user
        if not user:
            await callback.answer()
            return
        uid = int(user.id)
        ok = await self.user_storage.upsert_greeter_meeting_rsvp(
            meeting_key=gm.MEETING_KEY,
            user_id=uid,
            response=response,
        )
        if not ok:
            await callback.answer("Не удалось сохранить — попробуйте ещё раз", show_alert=True)
            return

        if self.message_copier:
            try:
                await self.message_copier.save_callback(
                    callback_query=callback,
                    subtype=f"gm_rsvp:{response}",
                )
            except Exception:
                pass

        await callback.answer("Спасибо!" if response == "coming" else "Поняли")
        ack = txt.ACK_COMING_HTML if response == "coming" else txt.ACK_CANT_HTML
        try:
            if callback.message:
                await callback.message.answer(ack, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.warning("[%s] ack uid=%s: %s", self.name, uid, e)
