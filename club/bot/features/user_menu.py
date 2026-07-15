"""Команда /menu — инлайн-меню возможностей бота для участника."""

from __future__ import annotations

import logging

from aiogram import Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.features.base import BaseFeature
from bot.filters import PRIVATE_INLINE_CALLBACK_ONLY
from bot.texts import ru_user_menu as menu_txt
from bot.utils.user_ui import CB_MAIN_MENU, render_user_screen, with_main_menu
from config import config

logger = logging.getLogger(__name__)

CB_PREFIX = "menu_act:"
CB_PAYMENT = f"{CB_PREFIX}payment"
CB_SUBS = f"{CB_PREFIX}subs"
CB_CLUB = f"{CB_PREFIX}club"
CB_SUPPORT = f"{CB_PREFIX}support"
CB_FEEDBACK = f"{CB_PREFIX}feedback"
CB_AFFILIATE = f"{CB_PREFIX}affiliate"
CB_BENEFIT = f"{CB_PREFIX}benefit"
CB_MEMBER_GIFT = f"{CB_PREFIX}member_gift"
CB_WISH_BOARD = f"{CB_PREFIX}wish_board"
CB_HOME = CB_MAIN_MENU


class UserMenuFeature(BaseFeature):
    name = "user_menu"

    def __init__(self, user_storage, feature_manager):
        super().__init__()
        self.user_storage = user_storage
        self.feature_manager = feature_manager
        self.bot = None

    def set_bot(self, bot):
        self.bot = bot

    async def initialize(self) -> None:
        logger.info("[%s] Фича инициализирована", self.name)

    async def teardown(self) -> None:
        logger.info("[%s] Фича остановлена", self.name)

    def register_handlers(self, dp: Dispatcher) -> None:
        dp.callback_query.register(
            self._on_menu_action,
            F.data.startswith(CB_PREFIX),
            PRIVATE_INLINE_CALLBACK_ONLY,
        )

    def build_keyboard(self):
        from aiogram.types import InlineKeyboardMarkup

        from bot.utils.inline_buttons import callback_button

        spec: list[tuple] = [
            (menu_txt.BTN_PAYMENT, CB_PAYMENT, None),
        ]
        if config.wish_board_active:
            spec.append((menu_txt.BTN_WISH_BOARD, CB_WISH_BOARD, "success"))
        spec.extend(
            [
                (menu_txt.BTN_SUBS, CB_SUBS, None),
            ]
        )
        if config.CLUB_GROUP_ID:
            spec.append((menu_txt.BTN_CLUB, CB_CLUB, None))
        spec.extend(
            [
                (menu_txt.BTN_SUPPORT, CB_SUPPORT, None),
                (menu_txt.BTN_FEEDBACK, CB_FEEDBACK, None),
                (menu_txt.BTN_AFFILIATE, CB_AFFILIATE, None),
                (menu_txt.BTN_BENEFIT, CB_BENEFIT, None),
                (menu_txt.BTN_MEMBER_GIFT, CB_MEMBER_GIFT, None),
            ]
        )

        return InlineKeyboardMarkup(
            inline_keyboard=[
                [callback_button(label, cb, style=style)]
                for label, cb, style in spec
            ]
        )

    async def show_menu(self, message: Message, *, edit: bool = False) -> None:
        await render_user_screen(
            message,
            text=menu_txt.MENU_TITLE_HTML,
            reply_markup=self.build_keyboard(),
            edit=edit,
            add_main_menu=False,
        )

    async def cmd_menu(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        await self.show_menu(message, edit=False)

    async def go_home(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        await self.show_menu(message, edit=True)

    async def _on_menu_action(self, callback: CallbackQuery, state: FSMContext) -> None:
        data = callback.data or ""
        await callback.answer()
        msg = callback.message
        if not msg or not callback.from_user:
            return

        if data == CB_HOME:
            await self.go_home(msg, state)
            return

        if data == CB_PAYMENT:
            payment = self.feature_manager.get("payment")
            if payment:
                await payment.show_tariffs(callback, state=state)
            return

        if data == CB_SUBS:
            subs = self.feature_manager.get("subscription_info")
            if subs:
                await subs.cmd_subs(msg, state, edit=True)
            return

        if data == CB_CLUB:
            club = self.feature_manager.get("club_group")
            if club:
                await club.present_club_access(
                    callback.from_user.id, msg, edit=True
                )
            return

        if data == CB_SUPPORT:
            support = self.feature_manager.get("support")
            if support:
                await support.start_support(msg, state, edit=True)
            return

        if data == CB_FEEDBACK:
            support = self.feature_manager.get("support")
            if support:
                await support.start_feedback(msg, state, edit=True)
            return

        if data == CB_AFFILIATE:
            referral = self.feature_manager.get("referral")
            if referral:
                await referral.show_affiliate_link(
                    msg, callback.from_user.id, edit=True
                )
            return

        if data == CB_BENEFIT:
            benefit = self.feature_manager.get("benefit")
            if benefit:
                await benefit.cmd_benefit(msg, edit=True)
            return

        if data == CB_MEMBER_GIFT:
            mgift = self.feature_manager.get("member_gift_extension")
            if mgift:
                await mgift.start_flow(msg, state, edit=True)
            return

        if data == CB_WISH_BOARD:
            wb = self.feature_manager.get("wish_board")
            if wb:
                await wb.show_hub(msg, edit=True)
            return
