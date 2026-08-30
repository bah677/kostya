"""Команда /reels: список архивных эфиров + кнопка «Создать сценарий» + фидбек сценариев."""

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
from telemost_audio.reels_director import (
    CB_REELS_FB,
    parse_feedback_cb,
    reach_keyboard,
)

logger = logging.getLogger(__name__)

CB_PREFIX = "reels:gen:"
_PAGE_SIZE = 10

_REACH_MAP = {
    "r1": 500,
    "r5": 3000,
    "r20": 12000,
    "r50": 25000,
}


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
        dp.callback_query.register(
            self._cb_feedback,
            F.data.startswith(CB_REELS_FB),
        )
        logger.info("[%s] /reels + feedback зарегистрированы", self.name)

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

    async def _cb_feedback(self, query: CallbackQuery) -> None:
        if query.from_user is None:
            return
        if not await is_admin_or_super(self.user_storage, query.from_user.id):
            await query.answer("Нет доступа", show_alert=True)
            return

        parsed = parse_feedback_cb(query.data or "")
        if not parsed:
            await query.answer("Неверная кнопка", show_alert=True)
            return
        action, scenario_id = parsed

        if not hasattr(self.user_storage, "set_reels_scenario_feedback"):
            await query.answer("БД ещё без таблицы сценариев", show_alert=True)
            return

        uid = int(query.from_user.id)

        if action in _REACH_MAP:
            ok = await self.user_storage.set_reels_scenario_feedback(
                scenario_id,
                status="published",
                user_id=uid,
                reach=_REACH_MAP[action],
            )
            await query.answer("Охват сохранён" if ok else "Ошибка")
            if query.message and ok:
                try:
                    await query.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass
            return

        if action == "ok":
            ok = await self.user_storage.set_reels_scenario_feedback(
                scenario_id, status="approved", user_id=uid
            )
            await query.answer("В работу ✓" if ok else "Ошибка")
            return

        if action == "no":
            ok = await self.user_storage.set_reels_scenario_feedback(
                scenario_id, status="rejected", user_id=uid
            )
            await query.answer("Отмечено 👎" if ok else "Ошибка")
            return

        if action == "pub":
            ok = await self.user_storage.set_reels_scenario_feedback(
                scenario_id, status="published", user_id=uid
            )
            await query.answer("Опубликовано — укажи охват")
            if query.message and ok:
                try:
                    await query.message.edit_reply_markup(
                        reply_markup=reach_keyboard(scenario_id)
                    )
                except Exception:
                    if query.message:
                        await query.message.answer(
                            "Охват этого ролика:",
                            reply_markup=reach_keyboard(scenario_id),
                        )
            return


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
