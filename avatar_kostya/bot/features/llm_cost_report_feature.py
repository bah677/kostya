"""Ночной отчёт расходов нейросетей → топик статистики админ-группы клуба."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.features.base import BaseFeature
from bot.filters.private_only import PRIVATE_CHAT
from bot.services.llm_cost_report import (
    build_day_report,
    format_report_html,
    resolve_usd_rub,
)
from config import config

logger = logging.getLogger(__name__)
_MSK = ZoneInfo("Europe/Moscow")


def _report_chat_id() -> int:
    return int(getattr(config, "LLM_COST_REPORT_CHAT_ID", 0) or 0)


def _report_topic_id() -> int:
    return int(getattr(config, "LLM_COST_REPORT_TOPIC_ID", 0) or 0)


def _enabled() -> bool:
    return bool(getattr(config, "LLM_COST_REPORT_ENABLED", True))


class LlmCostReportFeature(BaseFeature):
    name = "llm_cost_report"

    def __init__(self, user_storage: Any) -> None:
        super().__init__()
        self.user_storage = user_storage
        self._app: Any = None
        self._task: Optional[asyncio.Task] = None

    def set_bot(self, app: Any) -> None:
        self._app = app

    def register_handlers(self, dispatcher: Dispatcher) -> None:
        dispatcher.message.register(
            self.cmd_llm_cost, PRIVATE_CHAT, Command("llm_cost")
        )
        self.log("/llm_cost зарегистрирован")

    async def start_background_tasks(self) -> None:
        if not _enabled():
            self.log("LLM_COST_REPORT_ENABLED=0 — ночной отчёт выключен")
            return
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._nightly_loop(), name="llm_cost_report_nightly"
        )
        self.log("ночной отчёт расходов LLM: 00:05 МСК")

    async def stop_background_tasks(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _is_admin(self, user_id: int) -> bool:
        if config.SUPER_ADMIN_ID and user_id == config.SUPER_ADMIN_ID:
            return True
        return bool(await self.user_storage.is_bot_admin(user_id))

    async def cmd_llm_cost(self, message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        if not await self._is_admin(message.from_user.id):
            return
        day = date.today() - timedelta(days=1)
        raw = (command.args or "").strip()
        send_to_topic = False
        parts = raw.split()
        if parts and parts[-1].lower() == "send":
            send_to_topic = True
            parts = parts[:-1]
        if parts:
            try:
                day = datetime.strptime(parts[0][:10], "%Y-%m-%d").date()
            except ValueError:
                await message.answer(
                    "Формат: <code>/llm_cost</code>, "
                    "<code>/llm_cost 2026-09-28</code> или "
                    "<code>/llm_cost send</code>",
                    parse_mode="HTML",
                )
                return
        text = await self._build_text(day)
        await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
        if send_to_topic:
            ok = await self._send_to_stats_topic(text)
            await message.answer(
                "✅ В топик статистики" if ok else "⛔ Не отправилось в топик"
            )

    async def _nightly_loop(self) -> None:
        while True:
            try:
                now = datetime.now(_MSK)
                tomorrow = now.date() + timedelta(days=1)
                target = datetime.combine(tomorrow, time(0, 5), tzinfo=_MSK)
                delay = max(30.0, (target - now).total_seconds())
                await asyncio.sleep(delay)
                yesterday = datetime.now(_MSK).date() - timedelta(days=1)
                text = await self._build_text(yesterday)
                ok = await self._send_to_stats_topic(text)
                logger.info(
                    "[%s] nightly report day=%s sent=%s",
                    self.name,
                    yesterday,
                    ok,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("[%s] nightly loop: %s", self.name, e)
                await asyncio.sleep(300)

    async def _build_text(self, day: date) -> str:
        converter = getattr(self._app, "currency_converter", None) if self._app else None
        usd_rub = await resolve_usd_rub(converter)
        report = await build_day_report(
            self.user_storage, day, usd_rub=usd_rub
        )
        return format_report_html(report)

    async def _send_to_stats_topic(self, text: str) -> bool:
        chat_id = _report_chat_id()
        topic_id = _report_topic_id()
        if not chat_id:
            logger.error("[%s] LLM_COST_REPORT_CHAT_ID не задан", self.name)
            return False
        token = (getattr(config, "CLUB_BOT_TOKEN", None) or "").strip()
        if not token:
            logger.error("[%s] CLUB_BOT_TOKEN пуст — нечем слать в админ-группу", self.name)
            return False
        bot = Bot(token=token)
        try:
            kwargs = {}
            if topic_id:
                kwargs["message_thread_id"] = int(topic_id)
            await bot.send_message(
                chat_id,
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                **kwargs,
            )
            return True
        except Exception as e:
            logger.exception("[%s] send failed: %s", self.name, e)
            return False
        finally:
            await bot.session.close()
