"""Админ-панель /adm: меню по группам (только админ / суперадмин)."""

from __future__ import annotations

import logging
from typing import Any, Optional

from aiogram import Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.admin_guard import is_admin_or_super, is_super_admin_user_id
from bot.features.base import BaseFeature
from bot.services.admin_panel import (
    CB_HOME,
    CB_PREFIX,
    build_admin_panel_group,
    build_admin_panel_home,
    build_prayer_stats_keyboard,
    build_quick_report_keyboard_for,
    parse_admin_panel_group_cb,
    parse_admin_panel_quick_cb,
    parse_prayer_stats_ask_cb,
    parse_prayer_stats_cb,
)
from bot.services.admin_quick_reports import (
    build_last_campaigns_report_html,
)
from bot.services.bot_live_status import build_bot_live_status_html
from bot.services.prayer_usage_report import (
    build_prayer_stats_html,
    parse_user_prayer_date,
)
from bot.states import AdminPanelStates
from bot.texts.admin_panel_catalog import HelpTier
from bot.utils.admin_channel import resolved_admin_group_id

logger = logging.getLogger(__name__)


class AdminPanelFeature(BaseFeature):
    name = "admin_panel"

    def __init__(self, user_storage) -> None:
        super().__init__()
        self.user_storage = user_storage
        self._bot_app: Optional[Any] = None

    def set_bot(self, bot_app) -> None:
        self._bot_app = bot_app

    def register_handlers(self, dp: Dispatcher) -> None:
        private = F.chat.type == ChatType.PRIVATE
        dp.message.register(self._cmd_adm, private, Command("adm"))
        dp.message.register(self._cmd_adm, private, Command("admin"))
        dp.message.register(self._cmd_status, private, Command("status"))
        dp.message.register(self._cmd_status, private, Command("live"))
        dp.message.register(
            self._msg_prayer_stats_date,
            private,
            StateFilter(AdminPanelStates.waiting_prayer_stats_date),
            F.text,
        )
        dp.callback_query.register(
            self._cb_panel,
            F.data.startswith(f"{CB_PREFIX}:"),
        )
        gid = resolved_admin_group_id()
        if gid:
            admin_chat = F.chat.id == gid
            dp.message.register(self._cmd_adm, admin_chat, Command("adm"))
            dp.message.register(self._cmd_adm, admin_chat, Command("admin"))
            dp.message.register(self._cmd_status, admin_chat, Command("status"))
            dp.message.register(self._cmd_status, admin_chat, Command("live"))
            dp.message.register(
                self._msg_prayer_stats_date,
                admin_chat,
                StateFilter(AdminPanelStates.waiting_prayer_stats_date),
                F.text,
            )
        logger.info("[%s] /adm /status зарегистрированы", self.name)

    async def _resolve_tier(self, uid: int) -> HelpTier:
        if is_super_admin_user_id(uid):
            return "superadmin"
        return "admin"

    async def _cmd_adm(self, message: Message, state: FSMContext) -> None:
        if message.from_user is None or message.from_user.is_bot:
            return
        if not await is_admin_or_super(self.user_storage, message.from_user.id):
            return
        await state.clear()
        tier = await self._resolve_tier(message.from_user.id)
        text, kb = build_admin_panel_home(tier)
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    async def _cmd_status(self, message: Message) -> None:
        if message.from_user is None or message.from_user.is_bot:
            return
        if not await is_admin_or_super(self.user_storage, message.from_user.id):
            return
        text = await self._build_quick_report("live")
        await message.answer(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=build_quick_report_keyboard_for("live"),
            disable_web_page_preview=True,
        )

    async def _build_quick_report(self, key: str) -> str:
        pool = getattr(self.user_storage, "pool", None)
        if pool is None:
            return "❌ База данных недоступна."
        if key == "live":
            return await build_bot_live_status_html(pool, self._bot_app)
        if key == "mail3":
            return await build_last_campaigns_report_html(pool)
        if key == "prayer":
            return await build_prayer_stats_html(pool, screen="ov", period="30")
        return "❌ Неизвестный отчёт."

    async def _show_prayer_stats(
        self,
        *,
        screen: str,
        period: str,
        edit_message: Optional[Message] = None,
        reply_to: Optional[Message] = None,
    ) -> None:
        pool = getattr(self.user_storage, "pool", None)
        if pool is None:
            text = "❌ База данных недоступна."
        else:
            text = await build_prayer_stats_html(
                pool, screen=screen, period=period  # type: ignore[arg-type]
            )
        if len(text) > 4000:
            text = text[:3990] + "\n…"
        kb = build_prayer_stats_keyboard(screen, period)
        if edit_message is not None:
            await edit_message.edit_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
            return
        if reply_to is not None:
            await reply_to.answer(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )

    async def _msg_prayer_stats_date(self, message: Message, state: FSMContext) -> None:
        if message.from_user is None or message.from_user.is_bot:
            return
        if not await is_admin_or_super(self.user_storage, message.from_user.id):
            await state.clear()
            return
        raw = (message.text or "").strip()
        if raw.lower() in {"/cancel", "отмена", "cancel"}:
            await state.clear()
            await message.answer("Ок, ввод даты отменён. /adm")
            return
        day = parse_user_prayer_date(raw)
        if day is None:
            await message.answer(
                "Не разобрал дату. Пришлите <code>ДД.ММ.ГГГГ</code> "
                "или <code>ГГГГ-ММ-ДД</code> (или /cancel).",
                parse_mode=ParseMode.HTML,
            )
            return
        data = await state.get_data()
        screen = str(data.get("prayer_screen") or "ov")
        await state.clear()
        period = day.isoformat()
        await message.answer(f"⏳ Собираю отчёт за {day.strftime('%d.%m.%Y')}…")
        await self._show_prayer_stats(screen=screen, period=period, reply_to=message)

    async def _cb_panel(self, query: CallbackQuery, state: FSMContext) -> None:
        if query.from_user is None or query.message is None:
            await query.answer()
            return
        if not await is_admin_or_super(self.user_storage, query.from_user.id):
            await query.answer("⛔", show_alert=True)
            return
        tier = await self._resolve_tier(query.from_user.id)
        data = query.data or ""
        try:
            ask_screen = parse_prayer_stats_ask_cb(data)
            if ask_screen:
                await state.set_state(AdminPanelStates.waiting_prayer_stats_date)
                await state.update_data(prayer_screen=ask_screen)
                await query.answer()
                await query.message.answer(
                    "📅 Дата отчёта по молитвам\n\n"
                    "Пришлите день: <code>14.08.2026</code> или "
                    "<code>2026-08-14</code>.\n"
                    "Отмена: /cancel",
                    parse_mode=ParseMode.HTML,
                )
                return

            prayer = parse_prayer_stats_cb(data)
            if prayer:
                screen, period = prayer
                await state.clear()
                await query.answer("⏳")
                await self._show_prayer_stats(
                    screen=screen,
                    period=period,
                    edit_message=query.message,
                )
                return

            quick_key = parse_admin_panel_quick_cb(data)
            if quick_key:
                await state.clear()
                await query.answer("⏳")
                text = await self._build_quick_report(quick_key)
                kb = build_quick_report_keyboard_for(quick_key)
                await query.message.edit_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                    disable_web_page_preview=True,
                )
                return

            if data == CB_HOME:
                await state.clear()
                await query.answer()
                text, kb = build_admin_panel_home(tier)
                await query.message.edit_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
                return

            group_key = parse_admin_panel_group_cb(data)
            if group_key:
                await state.clear()
                await query.answer()
                text, kb = build_admin_panel_group(tier, group_key)
                await query.message.edit_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
                return

            await query.answer()
        except Exception as e:
            logger.exception("[%s] callback failed: %s", self.name, e)
            try:
                await query.answer("Ошибка", show_alert=True)
            except Exception:
                pass
