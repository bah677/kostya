"""Команда /reels: список архивных эфиров + кнопка «Создать сценарий»."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from aiogram import Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.admin_guard import is_admin_or_super
from bot.features.base import BaseFeature
from telemost_audio.recording_kind import KIND_LABELS, recording_kind_from_pending

logger = logging.getLogger(__name__)

CB_PREFIX = "reels:gen:"
_PAGE_SIZE = 10


def _short_date(row: dict) -> str:
    dt = row.get("created_at")
    if dt is None:
        return ""
    try:
        return dt.strftime("%d.%m.%Y")
    except Exception:
        return str(dt)[:10]


def _row_title(row: dict) -> str:
    clf = row.get("classification") or {}
    title = ""
    if isinstance(clf, dict):
        title = (clf.get("title") or "").strip()
    if not title:
        title = (row.get("subject") or "").strip()
    return title or "Без названия"


def _kind_label(row: dict) -> str:
    kind = recording_kind_from_pending(row)
    return KIND_LABELS.get(kind, "Эфир")


class ReelsFeature(BaseFeature):
    name = "reels_feature"

    def __init__(self, user_storage: Any) -> None:
        super().__init__()
        self.user_storage = user_storage

    def set_bot(self, app: Any) -> None:
        self._bot_app = app

    def register_handlers(self, dp: Dispatcher) -> None:
        private = F.chat.type == ChatType.PRIVATE
        dp.message.register(self._cmd_reels, private, Command("reels"))
        dp.callback_query.register(
            self._cb_gen,
            F.data.startswith(CB_PREFIX),
        )
        logger.info("[%s] /reels зарегистрирован", self.name)

    async def _cmd_reels(self, message: Message) -> None:
        if message.from_user is None or message.from_user.is_bot:
            return
        if not await is_admin_or_super(self.user_storage, message.from_user.id):
            return
        rows = await self.user_storage.list_telemost_for_reels(limit=_PAGE_SIZE * 3)
        if not rows:
            await message.answer("Нет архивных эфиров с расшифровкой.")
            return
        text, kb = _build_list(rows)
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    async def _cb_gen(self, query: CallbackQuery) -> None:
        await query.answer()
        if query.from_user is None:
            return
        if not await is_admin_or_super(self.user_storage, query.from_user.id):
            return

        raw_id = (query.data or "").removeprefix(CB_PREFIX).strip()
        try:
            pending_id = uuid.UUID(raw_id)
        except ValueError:
            if query.message:
                await query.message.answer("Неверный ID записи.")
            return

        row = await self.user_storage.get_telemost_pending_with_transcript(pending_id)
        if not row:
            if query.message:
                await query.message.answer("Запись не найдена.")
            return

        title = _row_title(row)
        if query.message:
            await query.message.answer(
                f"⏳ Генерирую Reels-сценарии для <b>{title}</b>…\n"
                "Результат появится в топике.",
                parse_mode=ParseMode.HTML,
            )

        # Запускаем pipeline — bot_app нужен для отправки результата
        bot_app = getattr(self, "_bot_app", None)
        if bot_app is None:
            logger.error("reels_feature: bot_app не привязан")
            return

        import asyncio
        from telemost_audio.reels_director import run_reels_brief_for_row

        asyncio.create_task(
            run_reels_brief_for_row(bot_app, row),
            name=f"reels_manual_{str(pending_id)[:8]}",
        )


def _build_list(rows: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["📋 <b>Архивные эфиры</b> — выбери запись для Reels-сценариев:\n"]
    buttons: list[list[InlineKeyboardButton]] = []
    shown = rows[:_PAGE_SIZE * 3]
    for row in shown:
        date = _short_date(row)
        title = _row_title(row)
        kind = _kind_label(row)
        label = f"[{kind}] {date} · {title}"[:64]
        lines.append(f"• {label}")
        buttons.append([
            InlineKeyboardButton(
                text=f"🎬 {label}",
                callback_data=f"{CB_PREFIX}{row['id']}",
            )
        ])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)
