"""Админ-панель /adm: меню по группам (только админ / суперадмин)."""

from __future__ import annotations

import logging

from aiogram import Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.admin_guard import is_admin_or_super, is_super_admin_user_id
from bot.features.base import BaseFeature
from bot.services.admin_panel import (
    CB_HOME,
    CB_PREFIX,
    build_admin_panel_group,
    build_admin_panel_home,
    parse_admin_panel_group_cb,
)
from bot.texts.admin_panel_catalog import HelpTier
from bot.utils.admin_channel import resolved_admin_group_id

logger = logging.getLogger(__name__)


class AdminPanelFeature(BaseFeature):
    name = "admin_panel"

    def __init__(self, user_storage) -> None:
        super().__init__()
        self.user_storage = user_storage

    def register_handlers(self, dp: Dispatcher) -> None:
        private = F.chat.type == ChatType.PRIVATE
        dp.message.register(self._cmd_adm, private, Command("adm"))
        dp.message.register(self._cmd_adm, private, Command("admin"))
        dp.callback_query.register(
            self._cb_panel,
            F.data.startswith(f"{CB_PREFIX}:"),
        )
        gid = resolved_admin_group_id()
        if gid:
            admin_chat = F.chat.id == gid
            dp.message.register(self._cmd_adm, admin_chat, Command("adm"))
            dp.message.register(self._cmd_adm, admin_chat, Command("admin"))
        logger.info("[%s] /adm зарегистрирован", self.name)

    async def _resolve_tier(self, uid: int) -> HelpTier:
        if is_super_admin_user_id(uid):
            return "superadmin"
        return "admin"

    async def _cmd_adm(self, message: Message) -> None:
        if message.from_user is None or message.from_user.is_bot:
            return
        if not await is_admin_or_super(self.user_storage, message.from_user.id):
            return
        tier = await self._resolve_tier(message.from_user.id)
        text, kb = build_admin_panel_home(tier)
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    async def _cb_panel(self, query: CallbackQuery) -> None:
        if query.from_user is None or query.message is None:
            await query.answer()
            return
        if not await is_admin_or_super(self.user_storage, query.from_user.id):
            await query.answer("⛔", show_alert=True)
            return
        tier = await self._resolve_tier(query.from_user.id)
        data = query.data or ""
        try:
            if data == CB_HOME:
                text, kb = build_admin_panel_home(tier)
            else:
                group_key = parse_admin_panel_group_cb(data)
                if not group_key:
                    await query.answer()
                    return
                text, kb = build_admin_panel_group(tier, group_key)
            await query.message.edit_text(
                text, parse_mode=ParseMode.HTML, reply_markup=kb
            )
            await query.answer()
        except Exception as e:
            logger.warning("[%s] panel edit failed: %s", self.name, e)
            await query.answer()
