"""Клубные рассылки в личку: дайджест + цитаты (пилот)."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.admin_guard import is_telegram_admin
from bot.features.base import BaseFeature
from bot.services.club_daily_digest import build_club_daily_digest
from bot.services.club_digest_dm_personalize import personalize_digest_for_user
from bot.services.club_engagement_policy import decide_club_outreach
from bot.services.club_outreach_pilot import refresh_pilot_cohort, resolve_outreach_recipients
from bot.services.club_scripture_dm import (
    build_scripture_batch,
    commit_scripture_batch,
    personalize_scripture_for_user,
)
from bot.services.club_scripture_pulse import (
    DEFAULT_PULSE_HOURS,
    parse_pulse_hours,
    pick_random_pulse_minutes,
)
from bot.utils.telegram_errors import format_exception
from config import config

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")


class ClubOutreachDmFeature(BaseFeature):
    name = "club_outreach_dm"

    def __init__(self, user_storage, bot):
        super().__init__()
        self.user_storage = user_storage
        self.bot: Bot = bot
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._pulse_job_ids: list[str] = []

    def register_handlers(self, dp: Dispatcher) -> None:
        admin_private = F.chat.type == ChatType.PRIVATE
        dp.message.register(
            self._cmd_outreach_test,
            admin_private,
            Command("outreach_dm_test"),
        )
        dp.message.register(
            self._cmd_refresh_pilot,
            admin_private,
            Command("outreach_pilot_refresh"),
        )

    async def initialize(self) -> None:
        if not config.club_outreach_dm_active:
            logger.info("[%s] disabled", self.name)
            return
        if not (config.DEEPSEEK_API_KEY or "").strip():
            logger.warning("[%s] no DEEPSEEK_API_KEY", self.name)
            return

        await refresh_pilot_cohort(self.user_storage)

        self._scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
        self._scheduler.add_job(
            self._run_daily_digest,
            CronTrigger(
                hour=config.CLUB_DIGEST_HOUR,
                minute=config.CLUB_DIGEST_MINUTE,
                timezone="Europe/Moscow",
            ),
            id="club_outreach_digest_dm",
            replace_existing=True,
        )
        self._schedule_scripture_jobs()
        self._scheduler.add_job(
            self._schedule_scripture_jobs,
            CronTrigger(hour=0, minute=5, timezone="Europe/Moscow"),
            id="club_outreach_scripture_reschedule",
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info("[%s] scheduler started (pilot=%s)", self.name, config.CLUB_OUTREACH_DM_PILOT_ONLY)

    async def teardown(self) -> None:
        if self._scheduler:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
            self._scheduler = None

    def _schedule_scripture_jobs(self) -> None:
        if not self._scheduler:
            return
        for jid in self._pulse_job_ids:
            try:
                self._scheduler.remove_job(jid)
            except Exception:
                pass
        self._pulse_job_ids.clear()

        hours = parse_pulse_hours(config.CLUB_SCRIPTURE_PULSE_HOURS)
        minutes = pick_random_pulse_minutes(
            minute_min=config.CLUB_SCRIPTURE_PULSE_MINUTE_MIN,
            minute_max=config.CLUB_SCRIPTURE_PULSE_MINUTE_MAX,
            pulse_hours=hours,
        )
        for hour in hours:
            minute = minutes[hour]
            jid = f"club_outreach_scripture_{hour}"
            self._scheduler.add_job(
                self._run_scripture_slot,
                CronTrigger(hour=hour, minute=minute, timezone="Europe/Moscow"),
                id=jid,
                replace_existing=True,
                kwargs={"slot_hour": hour},
            )
            self._pulse_job_ids.append(jid)

    async def _run_daily_digest(self) -> None:
        api_key = (config.DEEPSEEK_API_KEY or "").strip()
        pool = self.user_storage.pool
        if not pool or not api_key:
            return

        result = await build_club_daily_digest(
            pool,
            club_group_id=config.CLUB_GROUP_ID,
            api_key=api_key,
            digest_topic_id=config.CLUB_DIGEST_TOPIC_ID,
            min_messages=config.CLUB_DIGEST_MIN_MESSAGES,
            min_participants=config.CLUB_DIGEST_MIN_PARTICIPANTS,
            user_storage=self.user_storage,
        )
        if result.skipped:
            logger.info("[%s] digest batch skipped: %s", self.name, result.skip_reason)
            return

        recipients = await resolve_outreach_recipients(self.user_storage)
        sent = skipped = failed = 0
        today = date.today()

        for uid in recipients:
            decision = await decide_club_outreach(
                self.user_storage, uid, kind="digest", api_key=api_key
            )
            if not decision.allow:
                skipped += 1
                continue

            slug = f"club_digest_dm_{today.isoformat()}"
            if not await self.user_storage.try_claim_subscription_outreach(uid, slug, today):
                skipped += 1
                continue

            user = await self.user_storage.get_user(uid)
            fn = (user or {}).get("first_name")
            html = await personalize_digest_for_user(
                self.user_storage,
                user_id=uid,
                base_digest_html=result.html,
                api_key=api_key,
                first_name=fn,
            )
            if not html:
                html = result.html

            ok = await self._send_dm(uid, html)
            if ok:
                sent += 1
                await self.user_storage.increment_proactive_sent_today(uid)
                await self.user_storage.touch_outreach_dm_sent(uid, kind="digest")
                await self.user_storage.log_member_profile_event(
                    uid, "club_digest_dm_sent", meta={"pilot": config.CLUB_OUTREACH_DM_PILOT_ONLY}
                )
            else:
                failed += 1
            await asyncio.sleep(0.35)

        logger.info(
            "[%s] digest DM done sent=%s skip=%s fail=%s recipients=%s",
            self.name,
            sent,
            skipped,
            failed,
            len(recipients),
        )

    async def _run_scripture_slot(self, *, slot_hour: int) -> None:
        api_key = (config.DEEPSEEK_API_KEY or "").strip()
        pool = self.user_storage.pool
        if not pool or not api_key:
            return

        batch = await build_scripture_batch(
            pool,
            self.user_storage,
            club_group_id=config.CLUB_GROUP_ID,
            api_key=api_key,
            slot_hour=slot_hour,
            digest_topic_id=config.CLUB_DIGEST_TOPIC_ID,
        )
        if batch.skipped:
            logger.info("[%s] scripture batch skip slot=%s: %s", self.name, slot_hour, batch.skip_reason)
            return

        recipients = await resolve_outreach_recipients(self.user_storage)
        sent = skipped = failed = 0
        today = date.today()

        for uid in recipients:
            decision = await decide_club_outreach(
                self.user_storage,
                uid,
                kind="scripture",
                slot_hour=slot_hour,
                api_key=api_key,
            )
            if not decision.allow:
                skipped += 1
                continue

            slug = f"club_scripture_dm_{today.isoformat()}_{slot_hour}"
            if not await self.user_storage.try_claim_subscription_outreach(uid, slug, today):
                skipped += 1
                continue

            user = await self.user_storage.get_user(uid)
            fn = (user or {}).get("first_name")
            html = await personalize_scripture_for_user(
                self.user_storage,
                user_id=uid,
                batch=batch,
                api_key=api_key,
                first_name=fn,
            )
            if not html:
                skipped += 1
                continue

            ok = await self._send_dm(uid, html)
            if ok:
                sent += 1
                await self.user_storage.increment_proactive_sent_today(uid)
                await self.user_storage.touch_outreach_dm_sent(uid, kind="scripture")
                await self.user_storage.log_member_profile_event(
                    uid,
                    "club_scripture_dm_sent",
                    meta={"slot_hour": slot_hour},
                )
            else:
                failed += 1
            await asyncio.sleep(0.35)

        if sent > 0:
            commit_scripture_batch(batch)

        logger.info(
            "[%s] scripture DM slot=%s sent=%s skip=%s fail=%s",
            self.name,
            slot_hour,
            sent,
            skipped,
            failed,
        )

    async def _send_dm(self, user_id: int, html: str) -> bool:
        try:
            await self.bot.send_message(
                chat_id=user_id,
                text=html,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return True
        except Exception as e:
            logger.warning("[%s] DM uid=%s: %s", self.name, user_id, format_exception(e))
            return False

    async def _cmd_refresh_pilot(self, message: Message) -> None:
        uid = message.from_user.id if message.from_user else 0
        if not await is_telegram_admin(self.user_storage, uid):
            return
        ids = await refresh_pilot_cohort(self.user_storage)
        await message.answer(f"✅ Pilot cohort: {len(ids)} users\n" + ", ".join(str(i) for i in ids[:15]) + ("…" if len(ids) > 15 else ""))

    async def _cmd_outreach_test(self, message: Message, command: CommandObject) -> None:
        uid = message.from_user.id if message.from_user else 0
        if not await is_telegram_admin(self.user_storage, uid):
            return
        args = (command.args or "").strip().lower()
        if args == "digest":
            await self._run_daily_digest()
            await message.answer("✅ Digest DM run triggered")
        elif args.startswith("scripture"):
            parts = args.split()
            hour = int(parts[1]) if len(parts) > 1 else datetime.now(MSK).hour
            await self._run_scripture_slot(slot_hour=hour)
            await message.answer(f"✅ Scripture DM slot {hour} triggered")
        else:
            await message.answer(
                "<code>/outreach_dm_test digest</code>\n"
                "<code>/outreach_dm_test scripture [hour]</code>\n"
                "<code>/outreach_pilot_refresh</code>",
                parse_mode=ParseMode.HTML,
            )
