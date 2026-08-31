"""Сбор на подводное ружьё: deep-link /start gun (отдельная схема speargun)."""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional

from aiogram import Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.features.base import BaseFeature
from bot.filters import PRIVATE_CHAT_ONLY, PRIVATE_INLINE_CALLBACK_ONLY
from bot.payments.yookassa_service import YooKassaService
from bot.utils.user_ui import render_user_screen
from config import config

logger = logging.getLogger(__name__)

AMOUNT_PRESETS = {
    "RUB": [500, 1000, 5000],
    "USD": [5, 10, 50],
    "EUR": [5, 10, 50],
}
MIN_AMOUNT = {"RUB": 100, "USD": 1, "EUR": 1}
CURRENCY_LABEL = {
    "RUB": "🇷🇺 Рубли",
    "USD": "🇺🇸 Доллары",
    "EUR": "🇪🇺 Евро",
}
CURRENCY_SYMBOL = {"RUB": "₽", "USD": "$", "EUR": "€"}

CB_CUR = "sgf_cur:"
CB_AMT = "sgf_amt:"
CB_CUSTOM = "sgf_custom:"
CB_BACK = "sgf_back"


class SpeargunFundStates(StatesGroup):
    waiting_custom_amount = State()


def _deep_link() -> str:
    return (getattr(config, "SPEARGUN_DEEP_LINK", None) or "gun").strip().lower() or "gun"


def _enabled() -> bool:
    return bool(getattr(config, "speargun_fund_active", False))


def _q2(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _to_rub(amount: Decimal, currency: str) -> Decimal:
    cur = currency.upper()
    if cur == "RUB":
        return _q2(amount)
    if cur == "USD":
        return _q2(amount * Decimal(str(getattr(config, "SPEARGUN_USD_TO_RUB", 90))))
    if cur == "EUR":
        return _q2(amount * Decimal(str(getattr(config, "SPEARGUN_EUR_TO_RUB", 100))))
    raise ValueError(currency)


class SpeargunFundFeature(BaseFeature):
    name = "speargun_fund"

    def __init__(self, user_storage, feature_manager=None):
        super().__init__()
        self.user_storage = user_storage
        self.feature_manager = feature_manager
        self._bot = None
        self._yk: Optional[YooKassaService] = None

    def set_bot(self, bot_app) -> None:
        self._bot = getattr(bot_app, "bot", bot_app)

    def register_handlers(self, dp: Dispatcher) -> None:
        dp.callback_query.register(
            self._on_callback,
            F.data.startswith("sgf_"),
            PRIVATE_INLINE_CALLBACK_ONLY,
        )

    async def try_open_from_start(
        self, message: Message, state: FSMContext, param: str
    ) -> bool:
        if not _enabled():
            return False
        p = (param or "").strip().lower()
        if p not in {_deep_link(), "speargun", "gun", "podvodnoe"}:
            return False
        await state.clear()
        await self.show_intro(message, edit=False)
        return True

    async def show_intro(self, message: Message, *, edit: bool = False) -> None:
        totals = await self.user_storage.speargun_totals()
        title = getattr(
            config,
            "SPEARGUN_CAMPAIGN_TITLE",
            "Сбор на подводное ружьё для Константина",
        )
        goal = getattr(
            config,
            "SPEARGUN_CAMPAIGN_GOAL",
            "Константин давно мечтает о настоящем подводном ружье. "
            "Давайте скинемся вместе — любая сумма поможет приблизить этот момент.",
        )
        text = (
            f"🔫 <b>{title}</b>\n\n"
            f"{goal}\n\n"
            f"Уже собрали: <b>{totals['raised_rub']:.0f} ₽</b>"
            f" · поддержали: <b>{totals['donors']}</b>\n\n"
            f"Выберите удобную валюту — дальше сумма и оплата в пару нажатий 💛"
        )
        await render_user_screen(
            message,
            text=text,
            reply_markup=self._currency_kb(),
            edit=edit,
            add_main_menu=True,
        )

    def _currency_kb(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=CURRENCY_LABEL[c], callback_data=f"{CB_CUR}{c}"
                    )
                ]
                for c in ("RUB", "USD", "EUR")
            ]
        )

    def _amount_kb(self, currency: str) -> InlineKeyboardMarkup:
        sym = CURRENCY_SYMBOL[currency]
        rows = [
            [
                InlineKeyboardButton(
                    text=f"{amt} {sym}",
                    callback_data=f"{CB_AMT}{currency}:{amt}",
                )
            ]
            for amt in AMOUNT_PRESETS[currency]
        ]
        rows.append(
            [
                InlineKeyboardButton(
                    text="✏️ Другая сумма",
                    callback_data=f"{CB_CUSTOM}{currency}",
                )
            ]
        )
        rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=CB_BACK)])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def _on_callback(self, callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.data or not callback.message:
            await callback.answer()
            return
        data = callback.data
        if data == CB_BACK:
            await state.clear()
            await self.show_intro(callback.message, edit=True)
            await callback.answer()
            return
        if data.startswith(CB_CUR):
            currency = data[len(CB_CUR) :].upper()
            if currency not in AMOUNT_PRESETS:
                await callback.answer("Неизвестная валюта", show_alert=True)
                return
            await state.clear()
            await render_user_screen(
                callback.message,
                text=f"Валюта: <b>{CURRENCY_LABEL[currency]}</b>\nВыберите сумму:",
                reply_markup=self._amount_kb(currency),
                edit=True,
                add_main_menu=True,
            )
            await callback.answer()
            return
        if data.startswith(CB_CUSTOM):
            currency = data[len(CB_CUSTOM) :].upper()
            await state.set_state(SpeargunFundStates.waiting_custom_amount)
            await state.update_data(sgf_currency=currency)
            mn = MIN_AMOUNT[currency]
            await render_user_screen(
                callback.message,
                text=f"Введите сумму в {CURRENCY_SYMBOL[currency]} (минимум {mn}):",
                edit=True,
                add_main_menu=True,
            )
            await callback.answer()
            return
        if data.startswith(CB_AMT):
            rest = data[len(CB_AMT) :]
            currency, raw = rest.split(":", 1)
            await state.clear()
            await callback.answer()
            await self._create_pay(
                callback.message,
                user=callback.from_user,
                currency=currency.upper(),
                amount=Decimal(raw),
                edit=True,
            )
            return
        await callback.answer()

    async def handle_custom_amount(
        self, message: Message, state: FSMContext, text: str
    ) -> None:
        data = await state.get_data()
        currency = (data.get("sgf_currency") or "RUB").upper()
        raw = re.sub(r"[^\d.]", "", (text or "").replace(",", ".").strip())
        try:
            amount = Decimal(raw)
        except (InvalidOperation, ValueError):
            await message.answer("Нужно число, например 1500")
            return
        if amount < Decimal(MIN_AMOUNT[currency]):
            await message.answer(
                f"Минимум {MIN_AMOUNT[currency]} {CURRENCY_SYMBOL[currency]}"
            )
            return
        await state.clear()
        await self._create_pay(
            message,
            user=message.from_user,
            currency=currency,
            amount=amount,
            edit=False,
        )

    def _yk(self) -> YooKassaService:
        if self._yk is None:
            self._yk = YooKassaService()
        return self._yk

    async def _create_pay(
        self,
        message: Message,
        *,
        user,
        currency: str,
        amount: Decimal,
        edit: bool,
    ) -> None:
        amount_rub = _to_rub(amount, currency)
        sym = CURRENCY_SYMBOL[currency]
        bot_username = (config.TELEGRAM_BOT_USERNAME or "Talk_God_Bot").lstrip("@")
        donation_id = await self.user_storage.speargun_create_donation(
            source="bot",
            amount=amount,
            currency=currency,
            amount_rub=amount_rub,
            telegram_user_id=user.id,
            telegram_username=user.username,
            donor_name=(user.full_name or "")[:120] or None,
            meta={"deep_link": _deep_link()},
        )
        if not donation_id:
            await render_user_screen(
                message, text="Не удалось создать запись платежа.", edit=edit
            )
            return
        try:
            url, payment_id, _ = await self._yk().create_payment(
                amount=float(amount_rub),
                description=f"Speargun · {amount} {currency}",
                user_id=user.id,
                payment_type="one_time",
                bot_username=bot_username,
            )
        except Exception as e:
            logger.exception("speargun yookassa: %s", e)
            await render_user_screen(
                message, text=f"Не удалось создать платёж: {e}", edit=edit
            )
            return
        if not url or not payment_id:
            await render_user_screen(
                message, text="ЮKassa не вернула ссылку на оплату.", edit=edit
            )
            return
        await self.user_storage.speargun_attach_yookassa(
            donation_id, yookassa_payment_id=payment_id, confirmation_url=url
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💳 Оплатить", url=url)],
                [InlineKeyboardButton(text="◀️ К сбору", callback_data=CB_BACK)],
            ]
        )
        await render_user_screen(
            message,
            text=(
                f"Готово к оплате: <b>{amount} {sym}</b> (≈ {amount_rub:.0f} ₽)\n\n"
                f"Нажмите кнопку — откроется ЮKassa.\n"
                f"Спасибо! 🔫"
            ),
            reply_markup=kb,
            edit=edit,
            add_main_menu=True,
        )
