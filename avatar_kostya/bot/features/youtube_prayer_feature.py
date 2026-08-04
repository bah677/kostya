"""Команда /yt_prayer + ежедневный запуск в 03:00 MSK."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from aiogram import Dispatcher
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.features.base import BaseFeature
from bot.filters.private_only import PRIVATE_CHAT
from config import config
from youtube_prayer.pipeline import run_daily_youtube_prayer_pipeline

logger = logging.getLogger(__name__)

_MSK = ZoneInfo("Europe/Moscow")


class YoutubePrayerFeature(BaseFeature):
    name = "youtube_prayer"

    def __init__(self) -> None:
        super().__init__()
        self._app: Any = None
        self._task: Optional[asyncio.Task] = None
        self._run_lock = asyncio.Lock()

    def set_bot(self, app: Any) -> None:
        self._app = app

    async def _is_admin(self, user_id: int) -> bool:
        if config.SUPER_ADMIN_ID and user_id == config.SUPER_ADMIN_ID:
            return True
        if self._app and await self._app.user_storage.is_bot_admin(user_id):
            return True
        return False

    def register_handlers(self, dispatcher: Dispatcher) -> None:
        if not getattr(config, "YT_PRAYER_ENABLED", True):
            self.log("YT_PRAYER_ENABLED=0 — хендлеры не регистрируются")
            return
        dispatcher.message.register(
            self.cmd_yt_prayer, PRIVATE_CHAT, Command("yt_prayer")
        )
        dispatcher.message.register(
            self.cmd_yt_prayer, PRIVATE_CHAT, Command("yt_prayer_run")
        )
        self.log("/yt_prayer зарегистрирован")

    async def start_background_tasks(self) -> None:
        if not getattr(config, "YT_PRAYER_ENABLED", True):
            return
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._daily_loop(), name="youtube_prayer_daily"
        )
        hour = int(getattr(config, "YT_PRAYER_HOUR_MSK", 3) or 3)
        self.log(f"ежедневный запуск в {hour:02d}:00 MSK")

    async def stop_background_tasks(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def cmd_yt_prayer(self, message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        if not await self._is_admin(message.from_user.id):
            return
        args = (command.args or "").strip().lower()
        force = args in {"force", "forced", "1", "yes"}
        await message.answer(
            "Запускаю пайплайн YouTube-молитв"
            + (" (force)" if force else "")
            + "… Это может занять 15–40 минут."
        )
        asyncio.create_task(
            self._run_safe(force=force, progress_chat_id=message.chat.id),
            name="yt_prayer_manual",
        )

    async def _daily_loop(self) -> None:
        hour = int(getattr(config, "YT_PRAYER_HOUR_MSK", 3) or 3) % 24
        while True:
            try:
                delay = _seconds_until_msk(hour, 0)
                logger.info(
                    "[%s] sleep %.0fs until %02d:00 MSK",
                    self.name,
                    delay,
                    hour,
                )
                await asyncio.sleep(delay)
                await self._run_safe(force=False, progress_chat_id=None)
                # защита от повторного срабатывания в ту же минуту
                await asyncio.sleep(70)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("[%s] daily loop error: %s", self.name, e)
                await asyncio.sleep(300)

    async def _run_safe(
        self, *, force: bool, progress_chat_id: Optional[int]
    ) -> None:
        if self._run_lock.locked():
            if progress_chat_id and self._app:
                try:
                    await self._app.bot.send_message(
                        progress_chat_id,
                        "Пайплайн уже выполняется — дождитесь окончания.",
                    )
                except Exception:
                    pass
            return
        async with self._run_lock:
            if not self._app:
                return
            chat_id = int(getattr(config, "YT_PRAYER_CHAT_ID", 0) or 0)
            topic_id = int(getattr(config, "YT_PRAYER_TOPIC_ID", 0) or 0)
            if not chat_id:
                logger.error("[%s] YT_PRAYER_CHAT_ID не задан", self.name)
                return
            work = Path(
                getattr(config, "YT_PRAYER_WORK_DIR", None)
                or "data/youtube_prayer"
            )
            if not work.is_absolute():
                work = Path(__file__).resolve().parents[2] / work
            count = int(getattr(config, "YT_PRAYER_COUNT", 3) or 3)
            history_days = int(
                getattr(config, "YT_PRAYER_TREND_HISTORY_DAYS", 14) or 14
            )
            en_enabled = bool(getattr(config, "YT_PRAYER_EN_ENABLED", True))
            en_count = int(getattr(config, "YT_PRAYER_EN_COUNT", 1) or 1)
            en_voice = (
                getattr(config, "YT_PRAYER_EN_VOICE_ID", None)
                or "a4CnuaYbALRvW39mDitg"
            )
            result = await run_daily_youtube_prayer_pipeline(
                self._app.bot,
                chat_id=chat_id,
                topic_id=topic_id,
                work_root=work,
                count=count,
                force=force,
                progress_chat_id=progress_chat_id,
                history_days=history_days,
                en_enabled=en_enabled,
                en_count=en_count,
                en_voice_id=str(en_voice),
            )
            if progress_chat_id:
                if result.skipped:
                    msg = f"Уже есть готовый прогон за {result.day}. Добавьте force: /yt_prayer force"
                elif result.ok:
                    parts = []
                    if result.themes:
                        parts.append("RU: " + ", ".join(result.themes))
                    if result.themes_en:
                        parts.append("EN: " + ", ".join(result.themes_en))
                    msg = f"Готово за {result.day}. " + (" | ".join(parts) if parts else "ok")
                else:
                    msg = f"Ошибка: {result.error}"
                try:
                    await self._app.bot.send_message(progress_chat_id, msg)
                except Exception:
                    pass


def _seconds_until_msk(hour: int, minute: int) -> float:
    now = datetime.now(_MSK)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return max(5.0, (target - now).total_seconds())
