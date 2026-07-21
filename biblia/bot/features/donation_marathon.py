"""Админ-марафон сбора пожертвований + кнопка прогресса для пользователей."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from aiogram import Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.admin_guard import is_telegram_admin
from bot.features.base import BaseFeature
from bot.services.donation_marathon_progress import (
    accept_flags,
    format_money,
    marathon_progress_html,
)
from bot.utils.admin_channel import send_admin_html_message
from config import config

logger = logging.getLogger(__name__)

_CB_OPEN = "marathon_open"
_CB_PAY_RUB = "marathon_pay_rub"
_CB_PAY_USD = "marathon_pay_usd"
_CB_PAY_CRYPTO = "marathon_pay_crypto"
_CB_ACCEPT_TOGGLE = "marathon_acc_"  # marathon_acc_rub / usd / crypto
_CB_ACCEPT_DONE = "marathon_acc_done"
_CB_CONFIRM_YES = "marathon_confirm_yes"
_CB_CONFIRM_NO = "marathon_confirm_no"


class MarathonAdminStates(StatesGroup):
    name = State()
    goal_amount = State()
    goal_currency = State()
    accept_methods = State()
    description = State()
    confirm = State()
    crypto_amount = State()
    crypto_user = State()
    crypto_note = State()


class DonationMarathonFeature(BaseFeature):
    name = "donation_marathon"

    def __init__(self, user_storage, bot=None) -> None:
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot

    def set_bot(self, telegram_app) -> None:
        self.bot = telegram_app.bot if telegram_app else self.bot

    def register_handlers(self, dp: Dispatcher) -> None:
        dp.message.register(
            self.cmd_marathon,
            F.chat.type == ChatType.PRIVATE,
            Command("marathon"),
        )
        dp.message.register(
            self.cmd_marathon_start,
            F.chat.type == ChatType.PRIVATE,
            Command("marathon_start"),
        )
        dp.message.register(
            self.cmd_marathon_stop,
            F.chat.type == ChatType.PRIVATE,
            Command("marathon_stop"),
        )
        dp.message.register(
            self.cmd_marathon_crypto,
            F.chat.type == ChatType.PRIVATE,
            Command("marathon_crypto"),
        )

        dp.message.register(
            self._admin_name,
            StateFilter(MarathonAdminStates.name),
            F.text,
        )
        dp.message.register(
            self._admin_goal_amount,
            StateFilter(MarathonAdminStates.goal_amount),
            F.text,
        )
        dp.message.register(
            self._admin_description,
            StateFilter(MarathonAdminStates.description),
            F.text,
        )
        dp.message.register(
            self._admin_crypto_amount,
            StateFilter(MarathonAdminStates.crypto_amount),
            F.text,
        )
        dp.message.register(
            self._admin_crypto_user,
            StateFilter(MarathonAdminStates.crypto_user),
            F.text,
        )
        dp.message.register(
            self._admin_crypto_note,
            StateFilter(MarathonAdminStates.crypto_note),
            F.text,
        )

        dp.callback_query.register(
            self.on_callback,
            F.data.startswith("marathon_"),
        )

    async def _ensure_admin(self, message: Message) -> bool:
        uid = message.from_user.id if message.from_user else None
        if uid is None or not await is_telegram_admin(self.user_storage, uid):
            await message.answer(
                "⛔ Нет доступа. Telegram ID должен быть в таблице <code>admins</code>.",
                parse_mode=ParseMode.HTML,
            )
            return False
        return True

    async def cmd_marathon(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message):
            return
        m = await self.user_storage.get_active_donation_marathon()
        if not m:
            await message.answer(
                "Сейчас марафон не запущен.\n"
                "Создать: /marathon_start\n"
                "Остановить: /marathon_stop\n"
                "Крипта вручную: /marathon_crypto"
            )
            return
        text = await self._status_html(m)
        await message.answer(text, parse_mode=ParseMode.HTML)

    async def cmd_marathon_start(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message):
            return
        active = await self.user_storage.get_active_donation_marathon()
        if active:
            await message.answer(
                "Уже есть активный марафон. Сначала /marathon_stop или дождитесь автозавершения.",
                parse_mode=ParseMode.HTML,
            )
            return
        await state.clear()
        await state.set_state(MarathonAdminStates.name)
        await message.answer(
            "🎙️ <b>Новый марафон</b>\n\nВведите <b>название</b> (оно же текст синей кнопки):",
            parse_mode=ParseMode.HTML,
        )

    async def cmd_marathon_stop(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message):
            return
        await state.clear()
        active = await self.user_storage.get_active_donation_marathon()
        if not active:
            await message.answer("Активного марафона нет.")
            return
        ok = await self.user_storage.close_donation_marathon(
            int(active["id"]),
            close_reason="forced",
            status="cancelled",
        )
        if not ok:
            await message.answer("Не удалось остановить марафон.")
            return
        raised = await self.user_storage.get_marathon_raised_amount(int(active["id"]))
        await message.answer(
            f"⏹ Марафон «{active['name']}» принудительно завершён.\n"
            f"Собрано: {format_money(raised, active['goal_currency'])} "
            f"из {format_money(float(active['goal_amount']), active['goal_currency'])}."
        )
        await send_admin_html_message(
            self.bot,
            f"⏹ Марафон <b>{active['name']}</b> остановлен админом "
            f"(собрано {format_money(raised, active['goal_currency'])}).",
        )

    async def cmd_marathon_crypto(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message):
            return
        active = await self.user_storage.get_active_donation_marathon()
        if not active:
            await message.answer("Нет активного марафона.")
            return
        if not active.get("accept_crypto"):
            await message.answer("В этом марафоне крипта не включена.")
            return
        await state.set_state(MarathonAdminStates.crypto_amount)
        await state.update_data(marathon_id=int(active["id"]))
        cur = str(active["goal_currency"]).upper()
        await message.answer(
            f"Введите сумму взноса в валюте цели (<b>{cur}</b>), например <code>25</code>:",
            parse_mode=ParseMode.HTML,
        )

    async def _status_html(self, marathon: Dict[str, Any]) -> str:
        mid = int(marathon["id"])
        raised = await self.user_storage.get_marathon_raised_amount(mid)
        donors = await self.user_storage.get_marathon_donors_count(mid)
        return marathon_progress_html(marathon, raised=raised, donors=donors)

    async def _admin_name(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message):
            return
        name = (message.text or "").strip()
        if not name or len(name) > 64:
            await message.answer("Название 1–64 символа.")
            return
        await state.update_data(name=name)
        await state.set_state(MarathonAdminStates.goal_amount)
        await message.answer("Цель сбора — число (например <code>300</code>):", parse_mode=ParseMode.HTML)

    async def _admin_goal_amount(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message):
            return
        raw = (message.text or "").strip().replace(",", ".")
        try:
            amount = float(raw)
        except ValueError:
            await message.answer("Введите число.")
            return
        if amount <= 0:
            await message.answer("Сумма должна быть больше 0.")
            return
        await state.update_data(goal_amount=amount)
        await state.set_state(MarathonAdminStates.goal_currency)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="₽ RUB", callback_data="marathon_goal_rub"),
                    InlineKeyboardButton(text="$ USD", callback_data="marathon_goal_usd"),
                ],
                [InlineKeyboardButton(text="€ EUR", callback_data="marathon_goal_eur")],
            ]
        )
        await message.answer("Валюта цели:", reply_markup=kb)

    async def _admin_description(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message):
            return
        text = (message.text or "").strip()
        if not text:
            await message.answer("Текст не может быть пустым.")
            return
        if len(text) > 3500:
            await message.answer("Слишком длинный текст (макс. 3500).")
            return
        await state.update_data(description_html=text)
        await state.set_state(MarathonAdminStates.confirm)
        data = await state.get_data()
        preview = self._confirm_preview(data)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Запустить", callback_data=_CB_CONFIRM_YES),
                    InlineKeyboardButton(text="❌ Отмена", callback_data=_CB_CONFIRM_NO),
                ]
            ]
        )
        await message.answer(preview, parse_mode=ParseMode.HTML, reply_markup=kb)

    def _confirm_preview(self, data: Dict[str, Any]) -> str:
        methods: List[str] = []
        if data.get("accept_rub"):
            methods.append("RUB")
        if data.get("accept_usd"):
            methods.append("USD")
        if data.get("accept_crypto"):
            methods.append("крипта")
        return (
            "📋 <b>Проверка марафона</b>\n\n"
            f"• Название: <b>{data.get('name')}</b>\n"
            f"• Цель: <b>{format_money(float(data.get('goal_amount') or 0), data.get('goal_currency') or 'USD')}</b>\n"
            f"• Засчитывать: <b>{', '.join(methods) or '—'}</b>\n\n"
            f"{data.get('description_html') or ''}"
        )

    def _accept_keyboard(self, data: Dict[str, Any]) -> InlineKeyboardMarkup:
        def mark(flag: bool, label: str) -> str:
            return f"{'✅' if flag else '⬜'} {label}"

        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=mark(bool(data.get("accept_rub")), "Рубли"),
                        callback_data=f"{_CB_ACCEPT_TOGGLE}rub",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=mark(bool(data.get("accept_usd")), "Доллары"),
                        callback_data=f"{_CB_ACCEPT_TOGGLE}usd",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=mark(bool(data.get("accept_crypto")), "Крипта"),
                        callback_data=f"{_CB_ACCEPT_TOGGLE}crypto",
                    )
                ],
                [InlineKeyboardButton(text="➡️ Далее", callback_data=_CB_ACCEPT_DONE)],
            ]
        )

    async def on_callback(self, callback: CallbackQuery, state: FSMContext) -> None:
        data = callback.data or ""
        if data in ("marathon_goal_rub", "marathon_goal_usd", "marathon_goal_eur"):
            await self._on_goal_currency(callback, state, data)
            return
        if data.startswith(_CB_ACCEPT_TOGGLE) or data == _CB_ACCEPT_DONE:
            await self._on_accept_methods(callback, state, data)
            return
        if data in (_CB_CONFIRM_YES, _CB_CONFIRM_NO):
            await self._on_confirm(callback, state, data)
            return
        if data == _CB_OPEN:
            await self._user_open(callback)
            return
        if data in (_CB_PAY_RUB, _CB_PAY_USD, _CB_PAY_CRYPTO):
            await self._user_pay_method(callback, state, data)
            return
        await callback.answer()

    async def _on_goal_currency(
        self, callback: CallbackQuery, state: FSMContext, data: str
    ) -> None:
        uid = callback.from_user.id if callback.from_user else 0
        if not await is_telegram_admin(self.user_storage, uid):
            await callback.answer("Нет доступа", show_alert=True)
            return
        cur = {"marathon_goal_rub": "RUB", "marathon_goal_usd": "USD", "marathon_goal_eur": "EUR"}[
            data
        ]
        await state.update_data(
            goal_currency=cur,
            accept_rub=False,
            accept_usd=False,
            accept_crypto=False,
        )
        await state.set_state(MarathonAdminStates.accept_methods)
        st = await state.get_data()
        await callback.message.edit_text(
            "Что засчитывать в прогресс? Отметьте методы, затем «Далее»:",
            reply_markup=self._accept_keyboard(st),
        )
        await callback.answer()

    async def _on_accept_methods(
        self, callback: CallbackQuery, state: FSMContext, data: str
    ) -> None:
        uid = callback.from_user.id if callback.from_user else 0
        if not await is_telegram_admin(self.user_storage, uid):
            await callback.answer("Нет доступа", show_alert=True)
            return
        st = await state.get_data()
        if data == f"{_CB_ACCEPT_TOGGLE}rub":
            await state.update_data(accept_rub=not bool(st.get("accept_rub")))
        elif data == f"{_CB_ACCEPT_TOGGLE}usd":
            await state.update_data(accept_usd=not bool(st.get("accept_usd")))
        elif data == f"{_CB_ACCEPT_TOGGLE}crypto":
            await state.update_data(accept_crypto=not bool(st.get("accept_crypto")))
        elif data == _CB_ACCEPT_DONE:
            st = await state.get_data()
            if not (st.get("accept_rub") or st.get("accept_usd") or st.get("accept_crypto")):
                await callback.answer("Выберите хотя бы один метод", show_alert=True)
                return
            await state.set_state(MarathonAdminStates.description)
            await callback.message.edit_text(
                "Введите <b>текст марафона</b> (HTML можно).\n"
                "Его увидят при нажатии на кнопку вместе с прогрессом.",
                parse_mode=ParseMode.HTML,
            )
            await callback.answer()
            return
        st = await state.get_data()
        await callback.message.edit_reply_markup(reply_markup=self._accept_keyboard(st))
        await callback.answer()

    async def _on_confirm(
        self, callback: CallbackQuery, state: FSMContext, data: str
    ) -> None:
        uid = callback.from_user.id if callback.from_user else 0
        if not await is_telegram_admin(self.user_storage, uid):
            await callback.answer("Нет доступа", show_alert=True)
            return
        if data == _CB_CONFIRM_NO:
            await state.clear()
            await callback.message.edit_text("Создание марафона отменено.")
            await callback.answer()
            return
        st = await state.get_data()
        row = await self.user_storage.create_donation_marathon(
            name=str(st.get("name") or ""),
            description_html=str(st.get("description_html") or ""),
            goal_amount=float(st.get("goal_amount") or 0),
            goal_currency=str(st.get("goal_currency") or "USD"),
            accept_rub=bool(st.get("accept_rub")),
            accept_usd=bool(st.get("accept_usd")),
            accept_crypto=bool(st.get("accept_crypto")),
            created_by=uid,
        )
        await state.clear()
        if not row:
            await callback.message.edit_text(
                "❌ Не удалось создать (возможно, уже есть активный марафон)."
            )
            await callback.answer()
            return
        await callback.message.edit_text(
            f"✅ Марафон «{row['name']}» запущен (id={row['id']}).\n"
            "Под каждым ответом бота — синяя кнопка с названием."
        )
        await callback.answer("Запущен")
        await send_admin_html_message(
            self.bot,
            f"🎙️ Марафон <b>{row['name']}</b> запущен. "
            f"Цель: {format_money(float(row['goal_amount']), row['goal_currency'])}.",
        )

    async def _admin_crypto_amount(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message):
            return
        raw = (message.text or "").strip().replace(",", ".")
        try:
            amount = float(raw)
        except ValueError:
            await message.answer("Введите число.")
            return
        if amount <= 0:
            await message.answer("Сумма > 0.")
            return
        await state.update_data(crypto_amount=amount)
        await state.set_state(MarathonAdminStates.crypto_user)
        await message.answer(
            "Telegram user_id донора (или <code>0</code>, если неизвестен):",
            parse_mode=ParseMode.HTML,
        )

    async def _admin_crypto_user(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message):
            return
        raw = (message.text or "").strip()
        try:
            user_id = int(raw)
        except ValueError:
            await message.answer("Нужен целый user_id или 0.")
            return
        await state.update_data(crypto_user_id=user_id)
        await state.set_state(MarathonAdminStates.crypto_note)
        await message.answer("Комментарий (txid / «-»):")

    async def _admin_crypto_note(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message):
            return
        note = (message.text or "").strip()
        if note == "-":
            note = ""
        data = await state.get_data()
        mid = int(data["marathon_id"])
        marathon = await self.user_storage.get_donation_marathon(mid)
        if not marathon or marathon.get("status") != "active":
            await state.clear()
            await message.answer("Марафон уже не активен.")
            return
        amount = float(data["crypto_amount"])
        user_id = int(data.get("crypto_user_id") or 0)
        admin_id = message.from_user.id if message.from_user else None
        row = await self.user_storage.add_marathon_contribution(
            marathon_id=mid,
            user_id=user_id,
            amount_goal=amount,
            amount_original=amount,
            currency_original=str(marathon["goal_currency"]),
            payment_id=None,
            source="crypto_manual",
            note=note or None,
            created_by=admin_id,
        )
        await state.clear()
        if not row:
            await message.answer("❌ Не удалось записать взнос.")
            return
        raised = await self.user_storage.get_marathon_raised_amount(mid)
        await message.answer(
            f"✅ Крипто-взнос {format_money(amount, marathon['goal_currency'])} учтён.\n"
            f"Всего: {format_money(raised, marathon['goal_currency'])}."
        )
        closed = await self.maybe_autoclose_marathon(mid)
        if closed and user_id > 0 and self.bot:
            try:
                from bot.services.donation_marathon_progress import thank_you_remaining_html

                await self.bot.send_message(
                    user_id,
                    thank_you_remaining_html(marathon, raised=raised),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

    async def _user_open(self, callback: CallbackQuery) -> None:
        marathon = await self.user_storage.get_active_donation_marathon()
        if not marathon:
            await callback.answer("Марафон завершён", show_alert=True)
            return
        raised = await self.user_storage.get_marathon_raised_amount(int(marathon["id"]))
        donors = await self.user_storage.get_marathon_donors_count(int(marathon["id"]))
        text = marathon_progress_html(marathon, raised=raised, donors=donors)
        kb = self._pay_methods_keyboard(marathon)
        await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        await callback.answer()

    def _pay_methods_keyboard(self, marathon: Dict[str, Any]) -> InlineKeyboardMarkup:
        rub, usd, crypto = accept_flags(marathon)
        rows = []
        if rub:
            rows.append(
                [InlineKeyboardButton(text="🇷🇺 Рубли (карты РФ)", callback_data=_CB_PAY_RUB)]
            )
        if usd:
            rows.append(
                [InlineKeyboardButton(text="💵 Доллары (карты не РФ)", callback_data=_CB_PAY_USD)]
            )
        if crypto:
            rows.append(
                [InlineKeyboardButton(text="₿ Криптовалюта", callback_data=_CB_PAY_CRYPTO)]
            )
        rows.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="payment_cancel")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def _user_pay_method(
        self, callback: CallbackQuery, state: FSMContext, data: str
    ) -> None:
        marathon = await self.user_storage.get_active_donation_marathon()
        if not marathon:
            await callback.answer("Марафон завершён", show_alert=True)
            return
        rub, usd, crypto = accept_flags(marathon)
        payment_feature = None
        # feature_manager may be attached later; resolve via message bot app if needed
        from bot.features.donation_payment import DonationPaymentFeature

        # Access through global registration: callback handlers share same process
        # We inject payment feature in initialize via feature_manager if set
        payment_feature = getattr(self, "_payment_feature", None)
        if payment_feature is None:
            await callback.answer("Оплата временно недоступна", show_alert=True)
            return

        await state.update_data(
            donation_mode="one_time",
            marathon_id=int(marathon["id"]),
        )

        if data == _CB_PAY_RUB:
            if not rub:
                await callback.answer("Рубли не в сборе", show_alert=True)
                return
            await payment_feature._show_amounts(callback, state, "RUB")
            return
        if data == _CB_PAY_USD:
            if not usd:
                await callback.answer("Доллары не в сборе", show_alert=True)
                return
            await payment_feature._show_amounts(callback, state, "USD")
            return
        if data == _CB_PAY_CRYPTO:
            if not crypto:
                await callback.answer("Крипта не в сборе", show_alert=True)
                return
            await self._show_crypto_for_marathon(callback, marathon)
            return

    async def _show_crypto_for_marathon(
        self, callback: CallbackQuery, marathon: Dict[str, Any]
    ) -> None:
        address = (
            os.getenv("BIBLIA_CRYPTO_USDT_TRON_ADDRESS", "").strip()
            or "TTq5YQ8NHowe9zT4bqW7gW79kDeioFCnpu"
        )
        msg = (
            f"💎 <b>Крипто для марафона «{marathon['name']}»</b>\n\n"
            f"USDT TRC-20:\n<code>{address}</code>\n\n"
            "После перевода админ занесёт сумму в прогресс вручную. "
            "Можно написать в поддержку (/feedback) с txid."
        )
        await callback.message.edit_text(msg, parse_mode=ParseMode.HTML)
        await callback.answer()

    def bind_payment_feature(self, payment_feature) -> None:
        self._payment_feature = payment_feature

    async def maybe_autoclose_marathon(self, marathon_id: int) -> bool:
        marathon = await self.user_storage.get_donation_marathon(marathon_id)
        if not marathon or marathon.get("status") != "active":
            return False
        raised = await self.user_storage.get_marathon_raised_amount(marathon_id)
        goal = float(marathon["goal_amount"])
        if raised + 1e-9 < goal:
            return False
        ok = await self.user_storage.close_donation_marathon(
            marathon_id,
            close_reason="goal_reached",
            status="completed",
        )
        if ok:
            logger.info(
                "🎙️ Marathon %s auto-completed raised=%s goal=%s",
                marathon_id,
                raised,
                goal,
            )
            if self.bot:
                await send_admin_html_message(
                    self.bot,
                    f"🎉 Марафон <b>{marathon['name']}</b> завершён по цели! "
                    f"Собрано {format_money(raised, marathon['goal_currency'])}.",
                )
        return ok
