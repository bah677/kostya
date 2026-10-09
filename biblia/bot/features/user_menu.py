"""Команда /menu — инлайн-меню возможностей бота для пользователя."""

from __future__ import annotations

import logging

from aiogram import Dispatcher, F
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.features.base import BaseFeature
from bot.texts import user_menu as _user_menu_texts

menu_txt = _user_menu_texts()
from bot.utils.user_ui import CB_MAIN_MENU, render_user_screen

logger = logging.getLogger(__name__)

CB_PREFIX = "menu_act:"
CB_MORE = f"{CB_PREFIX}more"
CB_PRAYER = f"{CB_PREFIX}prayer"
CB_CHALLENGE = f"{CB_PREFIX}challenge"
CB_PAYMENT = f"{CB_PREFIX}payment"
CB_SUPPORT = f"{CB_PREFIX}support"
CB_FEEDBACK = f"{CB_PREFIX}feedback"
CB_AFFILIATE = f"{CB_PREFIX}affiliate"
CB_HOME = CB_MAIN_MENU

_PRIVATE_CB = F.message.chat.type == ChatType.PRIVATE


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
            _PRIVATE_CB,
        )

    def build_keyboard(self) -> InlineKeyboardMarkup:
        """Кнопки только тех фич, что реально подняты на этом языке.

        Раньше список был жёстким. На испанском боте это давало меню из семи
        пунктов, из которых работал один: остальные вели в фичи, которых в
        процессе нет, — нажатие просто ничего не делало. Хуже пустой кнопки
        только кнопка, которая молчит.

        Фича появится — пункт встанет на место сам, править здесь не придётся.
        """
        from bot.langs import feature_enabled

        # (нужная фича, текст, callback)
        candidates = (
            ("payment", menu_txt.BTN_PAYMENT, CB_PAYMENT),
            ("personal_prayer", menu_txt.BTN_PRAYER, CB_PRAYER),
            ("scripture_challenge", menu_txt.BTN_CHALLENGE, CB_CHALLENGE),
            ("frequent_questions", menu_txt.BTN_MORE, CB_MORE),
            ("support", menu_txt.BTN_SUPPORT, CB_SUPPORT),
            ("support", menu_txt.BTN_FEEDBACK, CB_FEEDBACK),
            ("referral", menu_txt.BTN_AFFILIATE, CB_AFFILIATE),
        )
        rows = [
            [InlineKeyboardButton(text=text, callback_data=cb)]
            for feature, text, cb in candidates
            if feature_enabled(feature) and self.feature_manager.get_optional(feature)
        ]
        return InlineKeyboardMarkup(inline_keyboard=rows)

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

        if data == CB_MORE:
            faq = self.feature_manager.get_optional("frequent_questions")
            if faq:
                await faq.show_more(msg, edit=True)
            return

        if data == CB_PRAYER:
            prayer = self.feature_manager.get_optional("personal_prayer")
            if prayer:
                await prayer.start_from_menu(msg, state, edit=True)
            return

        if data == CB_CHALLENGE:
            ch = self.feature_manager.get_optional("scripture_challenge")
            if ch:
                await ch.start_from_menu(
                    msg, state, user_id=callback.from_user.id, edit=True
                )
            return

        if data == CB_PAYMENT:
            payment = self.feature_manager.get_optional("payment")
            if payment:
                uid = callback.from_user.id
                try:
                    await payment.user_storage.set_prayer_voice_unlock_pending(
                        uid, False
                    )
                except Exception:
                    pass
                await payment.show_donation_menu(
                    msg, state=state, from_user_id=uid, edit=True
                )
            return

        if data == CB_SUPPORT:
            support = self.feature_manager.get_optional("support")
            if support:
                await support.start_support(msg, state, edit=True)
            return

        if data == CB_FEEDBACK:
            support = self.feature_manager.get_optional("support")
            if support:
                await support.start_feedback(msg, state, edit=True)
            return

        if data == CB_AFFILIATE:
            referral = self.feature_manager.get_optional("referral")
            if referral:
                await referral.show_affiliate_link(
                    msg, callback.from_user.id, edit=True
                )
            return
