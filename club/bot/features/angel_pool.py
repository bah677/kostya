"""Ангельский взнос: один платёж → случайные продления на доске добрых дел."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Union

from aiogram import Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.features.base import BaseFeature
from bot.filters import PRIVATE_INLINE_CALLBACK_ONLY
from bot.services.angel_pool_service import (
    compute_extension_slots,
    min_amount_for_currency,
    monthly_price_from_tariff,
    parse_donation_amount,
    pick_monthly_base_tariff,
    preset_amounts_for_currency,
)
from bot.states import AngelPoolStates
from bot.texts import ru_angel_pool as ap_txt
from bot.utils.inline_buttons import callback_button
from bot.utils.user_ui import render_user_screen, with_main_menu

logger = logging.getLogger(__name__)

CB_INTRO = "ap:intro"
CB_CUR_RUB = "ap:cur:rub"
CB_CUR_USD = "ap:cur:usd"
CB_CONFIRM = "ap:confirm"
CB_REAMOUNT = "ap:reamount"
CB_CUSTOM = "ap:custom"
CB_AMT_PREFIX = "ap:amt:"
CB_HUB = "wb:hub"


class AngelPoolFeature(BaseFeature):
    name = "angel_pool"

    def __init__(self, user_storage, feature_manager):
        super().__init__()
        self.user_storage = user_storage
        self.feature_manager = feature_manager
        self.bot = None

    def set_bot(self, bot) -> None:
        self.bot = bot

    async def initialize(self) -> None:
        logger.info("[%s] инициализирована", self.name)

    async def teardown(self) -> None:
        logger.info("[%s] остановлена", self.name)

    def register_handlers(self, dp: Dispatcher) -> None:
        dp.callback_query.register(
            self._on_callback,
            F.data.startswith("ap:"),
            PRIVATE_INLINE_CALLBACK_ONLY,
        )

    def _back_keyboard(self):
        return with_main_menu(
            [
                [callback_button(ap_txt.BTN_BACK_HUB, CB_HUB)],
            ]
        )

    def _amount_keyboard(self, currency: str):
        cur = (currency or "RUB").upper()
        rows = []
        for amount in preset_amounts_for_currency(cur):
            rows.append(
                [
                    callback_button(
                        ap_txt.preset_amount_label(amount, cur),
                        f"{CB_AMT_PREFIX}{amount}",
                        style="success",
                    )
                ]
            )
        rows.append([callback_button(ap_txt.BTN_CUSTOM_AMOUNT, CB_CUSTOM)])
        rows.append([callback_button(ap_txt.BTN_BACK_HUB, CB_HUB)])
        return with_main_menu(rows)

    async def show_intro(
        self, message: Message, state: FSMContext, *, edit: bool = False
    ) -> None:
        await state.clear()
        kb = with_main_menu(
            [
                [callback_button(ap_txt.BTN_CURRENCY_RUB, CB_CUR_RUB)],
                [callback_button(ap_txt.BTN_CURRENCY_INTL, CB_CUR_USD)],
                [callback_button(ap_txt.BTN_BACK_HUB, CB_HUB)],
            ]
        )
        await render_user_screen(
            message,
            text=ap_txt.INTRO_HTML,
            reply_markup=kb,
            edit=edit,
            add_main_menu=False,
        )

    async def _show_amount_choices(
        self,
        message: Message,
        state: FSMContext,
        currency: str,
        *,
        edit: bool,
    ) -> None:
        cur = (currency or "RUB").upper()
        await state.update_data(ap_currency=cur)
        await state.set_state(AngelPoolStates.waiting_amount)
        text = (
            ap_txt.CHOOSE_AMOUNT_USD_HTML
            if cur == "USD"
            else ap_txt.CHOOSE_AMOUNT_RUB_HTML
        )
        await render_user_screen(
            message,
            text=text,
            reply_markup=self._amount_keyboard(cur),
            edit=edit,
            add_main_menu=False,
        )

    async def _on_callback(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        await callback.answer()
        if not callback.message or not callback.data:
            return
        data = callback.data
        msg = callback.message

        if data == CB_INTRO:
            await self.show_intro(msg, state, edit=True)
            return

        if data == CB_CUR_RUB:
            await self._show_amount_choices(msg, state, "RUB", edit=True)
            return

        if data == CB_CUR_USD:
            await self._show_amount_choices(msg, state, "USD", edit=True)
            return

        if data.startswith(CB_AMT_PREFIX):
            raw = data[len(CB_AMT_PREFIX) :]
            try:
                amount = float(raw)
            except ValueError:
                return
            await self._apply_amount(msg, state, amount, edit=True)
            return

        if data == CB_CUSTOM:
            fsm = await state.get_data()
            cur = (fsm.get("ap_currency") or "RUB").upper()
            await state.set_state(AngelPoolStates.waiting_amount)
            text = (
                ap_txt.PROMPT_AMOUNT_USD_HTML
                if cur == "USD"
                else ap_txt.PROMPT_AMOUNT_RUB_HTML
            )
            await render_user_screen(
                msg,
                text=text,
                reply_markup=self._back_keyboard(),
                edit=True,
                add_main_menu=False,
            )
            return

        if data == CB_REAMOUNT:
            fsm = await state.get_data()
            cur = (fsm.get("ap_currency") or "RUB").upper()
            await self._show_amount_choices(msg, state, cur, edit=True)
            return

        if data == CB_CONFIRM:
            await self._start_checkout(callback, state)
            return

    async def handle_amount(
        self, message: Message, state: FSMContext, text: str
    ) -> None:
        fsm = await state.get_data()
        currency = (fsm.get("ap_currency") or "RUB").upper()
        amount = parse_donation_amount(text)
        if amount is None:
            await message.answer(ap_txt.ERR_AMOUNT_PARSE)
            return
        await self._apply_amount(message, state, amount, edit=False)

    async def _apply_amount(
        self,
        message: Union[Message, Any],
        state: FSMContext,
        amount: float,
        *,
        edit: bool,
    ) -> None:
        fsm = await state.get_data()
        currency = (fsm.get("ap_currency") or "RUB").upper()
        minimum = min_amount_for_currency(currency)
        if amount < minimum:
            err = (
                ap_txt.ERR_AMOUNT_TOO_LOW_USD
                if currency == "USD"
                else ap_txt.ERR_AMOUNT_TOO_LOW_RUB
            )
            if edit:
                await render_user_screen(
                    message,
                    text=err,
                    reply_markup=self._amount_keyboard(currency),
                    edit=True,
                    add_main_menu=False,
                )
            else:
                await message.answer(err)
            return

        preview = await self._build_preview(currency, amount)
        if not preview:
            await state.clear()
            if edit:
                await render_user_screen(
                    message,
                    text=ap_txt.ERR_TARIFF_UNAVAILABLE,
                    edit=True,
                )
            else:
                await message.answer(ap_txt.ERR_TARIFF_UNAVAILABLE)
            return

        await state.update_data(
            ap_amount=float(amount),
            ap_currency=currency,
            ap_slots=preview["slots"],
            ap_tariff_id=preview["tariff"]["id"],
            ap_tariff_name=preview["tariff"]["name"],
            ap_duration_days=preview["tariff"]["duration_days"],
        )
        await state.set_state(AngelPoolStates.confirming)

        kb = with_main_menu(
            [
                [callback_button(ap_txt.BTN_CONFIRM_PAY, CB_CONFIRM)],
                [callback_button(ap_txt.BTN_CHANGE_AMOUNT, CB_REAMOUNT)],
                [callback_button(ap_txt.BTN_BACK_HUB, CB_HUB)],
            ]
        )
        await render_user_screen(
            message,
            text=preview["text"],
            reply_markup=kb,
            edit=edit,
            add_main_menu=False,
        )

    async def _build_preview(
        self, currency: str, amount: float
    ) -> Optional[Dict[str, Any]]:
        tariffs = await self.user_storage.get_active_tariffs(tariff_type="base")
        tariff = pick_monthly_base_tariff(tariffs)
        if not tariff:
            return None
        monthly = monthly_price_from_tariff(tariff, currency)
        if not monthly:
            return None
        slots = compute_extension_slots(amount, monthly)
        cur_label = ap_txt.currency_label(currency)
        text = ap_txt.PREVIEW_HTML.format(
            amount=ap_txt.format_amount(amount, currency),
            currency_label=cur_label,
            slots=slots,
            slots_word=ap_txt.slots_word(slots),
            tariff_name=ap_txt.escape_name(tariff["name"]),
            duration_days=int(tariff.get("duration_days") or 30),
        )
        return {"tariff": tariff, "slots": slots, "text": text}

    async def _start_checkout(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        fsm = await state.get_data()
        amount = fsm.get("ap_amount")
        currency = (fsm.get("ap_currency") or "").upper()
        slots = fsm.get("ap_slots")
        tariff_id = fsm.get("ap_tariff_id")
        if not amount or not currency or not slots or not tariff_id:
            await render_user_screen(
                callback.message,
                text=ap_txt.ERR_SESSION,
                edit=True,
            )
            return

        payment = self.feature_manager.get("payment")
        if not payment:
            await render_user_screen(
                callback.message,
                text=ap_txt.ERR_TARIFF_UNAVAILABLE,
                edit=True,
            )
            return

        tariff = await self.user_storage.get_tariff_by_id(int(tariff_id))
        if not tariff:
            await render_user_screen(
                callback.message,
                text=ap_txt.ERR_TARIFF_UNAVAILABLE,
                edit=True,
            )
            return

        await payment.start_angel_pool_checkout(
            callback,
            state,
            amount=float(amount),
            currency_code=currency,
            slots=int(slots),
            tariff=tariff,
        )
