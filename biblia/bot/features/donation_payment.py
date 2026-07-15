"""
Донаты Biblia: разовые и ежемесячная поддержка (BZB RECURRING).
RUB/USD/EUR через env-провайдер; подписки всегда через BZB.
"""

from __future__ import annotations

import json
import logging
import os
from typing import List, Optional

from aiogram import Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.features.base import BaseFeature
from bot.payments.bzb_service import BZBCreatePaymentError
from bot.payments.payment_provider_router import resolve_donation_payment_service
from bot.utils.telegram_identity import resolve_telegram_bot_username
from config import config

logger = logging.getLogger(__name__)

ONE_TIME_AMOUNT_PRESETS: dict[str, List[int]] = {
    "RUB": [300, 500, 1000],
    "USD": [3, 10, 15],
    "EUR": [3, 10, 15],
}
SUBSCRIPTION_AMOUNT_PRESETS: dict[str, List[int]] = {
    "RUB": [300, 500, 1000],
    "USD": [3, 10, 15],
    "EUR": [3, 10, 15],
}

_MIN_AMOUNT = {"RUB": 100, "USD": 1, "EUR": 1}
_CURRENCY_SYMBOL = {"RUB": "₽", "USD": "$", "EUR": "€"}
_CURRENCY_BTN = {
    "RUB": "🇷🇺 Рубли (карты РФ)",
    "USD": "💵 Доллары (карты не РФ)",
    "EUR": "💶 Евро",
}


class DonationPaymentStates(StatesGroup):
    waiting_custom_rub_amount = State()
    waiting_custom_usd_amount = State()
    waiting_custom_eur_amount = State()


class DonationPaymentFeature(BaseFeature):
    name = "payment"

    def __init__(self, user_storage, yookassa_service, bzb_service, bot):
        super().__init__()
        self.user_storage = user_storage
        self.yookassa_service = yookassa_service
        self.bzb_service = bzb_service
        self.bot = bot

    async def initialize(self) -> None:
        logger.info(
            "[%s] Фича донатов инициализирована (recurring=%s)",
            self.name,
            self._recurring_enabled(),
        )

    def register_handlers(self, dp: Dispatcher) -> None:
        dp.callback_query.register(self.handle_callback, F.data.startswith("payment_"))
        dp.message.register(
            self.on_custom_amount_message,
            StateFilter(DonationPaymentStates.waiting_custom_rub_amount),
            F.text,
        )
        dp.message.register(
            self.on_custom_amount_message,
            StateFilter(DonationPaymentStates.waiting_custom_usd_amount),
            F.text,
        )
        dp.message.register(
            self.on_custom_amount_message,
            StateFilter(DonationPaymentStates.waiting_custom_eur_amount),
            F.text,
        )

    def _presets_for_mode(self, mode: str) -> dict[str, List[int]]:
        if mode == "monthly":
            return SUBSCRIPTION_AMOUNT_PRESETS
        return ONE_TIME_AMOUNT_PRESETS

    def _recurring_enabled(self) -> bool:
        return bool(config.DONATION_RECURRING_ENABLED)

    def _donation_intro_text(self) -> str:
        return (
            "🤝 **Поддержи развитие нашего проекта**\n\n"
            "Твоя поддержка поможет сделать его лучше для всех пользователей.\n"
            "Ты вкладываешь в Благое дело 🙏🏻\n\n"
        )

    def _mode_keyboard(self, *, show_subscription_mgmt: bool) -> InlineKeyboardMarkup:
        rows = []
        if self._recurring_enabled():
            rows.extend(
                [
                    [
                        InlineKeyboardButton(
                            text="📅 Ежемесячная поддержка",
                            callback_data="payment_mode_monthly",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="💳 Разовый платёж",
                            callback_data="payment_mode_one_time",
                        )
                    ],
                ]
            )
        if show_subscription_mgmt:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="📋 Моя подписка",
                        callback_data="payment_my_subscription",
                    )
                ]
            )
        rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="payment_cancel")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _currency_keyboard(
        self,
        *,
        include_crypto: bool,
        show_subscription_mgmt: bool = False,
        show_back: bool = True,
    ) -> InlineKeyboardMarkup:
        rows = [
            [InlineKeyboardButton(text=_CURRENCY_BTN["RUB"], callback_data="payment_currency_rub")],
            [InlineKeyboardButton(text=_CURRENCY_BTN["USD"], callback_data="payment_currency_usd")],
            [InlineKeyboardButton(text=_CURRENCY_BTN["EUR"], callback_data="payment_currency_eur")],
        ]
        if include_crypto:
            rows.append(
                [InlineKeyboardButton(text="₿ Криптовалюта", callback_data="payment_crypto")]
            )
        if show_subscription_mgmt:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="📋 Моя подписка",
                        callback_data="payment_my_subscription",
                    )
                ]
            )
        if show_back:
            rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="payment_back_mode")])
        rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="payment_cancel")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _amount_keyboard(self, currency: str, mode: str) -> InlineKeyboardMarkup:
        cur = currency.upper()
        presets = self._presets_for_mode(mode).get(cur, [])
        sym = _CURRENCY_SYMBOL.get(cur, cur)
        rows = [
            [
                InlineKeyboardButton(
                    text=f"{amt} {sym}",
                    callback_data=f"payment_{cur.lower()}_amount_{amt}",
                )
            ]
            for amt in presets
        ]
        rows.append(
            [
                InlineKeyboardButton(
                    text="✏️ Другая сумма",
                    callback_data=f"payment_{cur.lower()}_custom",
                )
            ]
        )
        rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="payment_back_currency")])
        rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="payment_cancel")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _cancel_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="payment_back_currency")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="payment_cancel")],
            ]
        )

    async def _bump_donation_menu_open(self, user_id: int) -> None:
        try:
            async with self.user_storage.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE users
                       SET donation_button_click = COALESCE(donation_button_click, 0) + 1
                     WHERE user_id = $1
                    """,
                    user_id,
                )
        except Exception as e:
            logger.debug("donation_button_click skip: %s", e)

    async def show_donation_menu(
        self,
        message: Message,
        *,
        state: Optional[FSMContext] = None,
        from_user_id: Optional[int] = None,
    ) -> None:
        uid = from_user_id or (message.from_user.id if message.from_user else 0)
        await self._bump_donation_menu_open(uid)
        active_sub = await self.user_storage.get_user_active_donation_subscription(uid)

        if not self._recurring_enabled():
            if state is not None:
                await state.update_data(donation_mode="one_time")
                await state.set_state(None)
            await message.answer(
                self._donation_intro_text() + "Выберите валюту для доната:",
                reply_markup=self._currency_keyboard(
                    include_crypto=True,
                    show_subscription_mgmt=bool(active_sub),
                    show_back=False,
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        await message.answer(
            self._donation_intro_text() + "Выберите формат поддержки:",
            reply_markup=self._mode_keyboard(show_subscription_mgmt=bool(active_sub)),
            parse_mode=ParseMode.MARKDOWN,
        )

    async def handle_callback(self, callback: CallbackQuery, state: FSMContext) -> None:
        data = callback.data or ""
        if data == "payment_start":
            await callback.answer()
            if callback.message:
                uid = callback.from_user.id if callback.from_user else 0
                await self.show_donation_menu(
                    callback.message, state=state, from_user_id=uid
                )
            return

        if data == "payment_mode_one_time":
            await state.update_data(donation_mode="one_time")
            await self._show_currency_step(callback, state, include_crypto=True)
            return
        if data == "payment_mode_monthly":
            if not self._recurring_enabled():
                await callback.answer(
                    "Ежемесячная поддержка сейчас недоступна",
                    show_alert=True,
                )
                return
            await state.update_data(donation_mode="monthly")
            await self._show_currency_step(callback, state, include_crypto=False)
            return
        if data == "payment_my_subscription":
            await self._show_my_subscription(callback)
            return
        if data == "payment_cancel_subscription":
            await self._confirm_cancel_subscription(callback)
            return
        if data == "payment_confirm_cancel_sub":
            await self._do_cancel_subscription(callback)
            return

        if data == "payment_currency_rub":
            await self._show_amounts(callback, state, "RUB")
        elif data == "payment_currency_usd":
            await self._show_amounts(callback, state, "USD")
        elif data == "payment_currency_eur":
            await self._show_amounts(callback, state, "EUR")
        elif data == "payment_crypto":
            await self._handle_crypto_donation(callback)
        elif data.startswith("payment_rub_amount_"):
            await self._create_payment_callback(callback, state, "RUB", int(data[19:]))
        elif data.startswith("payment_usd_amount_"):
            await self._create_payment_callback(callback, state, "USD", int(data[19:]))
        elif data.startswith("payment_eur_amount_"):
            await self._create_payment_callback(callback, state, "EUR", int(data[19:]))
        elif data == "payment_rub_custom":
            await self._ask_custom_amount(callback, state, "RUB")
        elif data == "payment_usd_custom":
            await self._ask_custom_amount(callback, state, "USD")
        elif data == "payment_eur_custom":
            await self._ask_custom_amount(callback, state, "EUR")
        elif data == "payment_back_mode":
            await self._back_to_mode(callback, state)
        elif data == "payment_back_currency":
            await self._back_to_currency(callback, state)
        elif data == "payment_cancel":
            await self._cancel(callback, state)
        else:
            await callback.answer("❌ Неизвестная команда")

    async def _show_currency_step(
        self, callback: CallbackQuery, state: FSMContext, *, include_crypto: bool
    ) -> None:
        await state.set_state(None)
        mode = (await state.get_data()).get("donation_mode", "one_time")
        uid = callback.from_user.id if callback.from_user else 0
        active_sub = await self.user_storage.get_user_active_donation_subscription(uid)
        if mode == "monthly":
            title = "📅 **Ежемесячная поддержка**\n\nВыберите валюту:"
        elif self._recurring_enabled():
            title = "💳 **Разовый платёж**\n\nВыберите валюту:"
        else:
            title = self._donation_intro_text() + "Выберите валюту для доната:"
        await callback.message.edit_text(
            title,
            reply_markup=self._currency_keyboard(
                include_crypto=include_crypto,
                show_subscription_mgmt=bool(active_sub) and not self._recurring_enabled(),
                show_back=self._recurring_enabled(),
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
        await callback.answer()

    async def _show_amounts(self, callback: CallbackQuery, state: FSMContext, currency: str) -> None:
        await state.update_data(currency=currency.lower())
        mode = (await state.get_data()).get("donation_mode", "one_time")
        await callback.message.edit_text(
            "💎 **Любая сумма будет ценна для проекта**",
            reply_markup=self._amount_keyboard(currency, mode),
            parse_mode=ParseMode.MARKDOWN,
        )
        await callback.answer()

    async def _ask_custom_amount(
        self, callback: CallbackQuery, state: FSMContext, currency: str
    ) -> None:
        cur = currency.upper()
        await state.update_data(currency=currency.lower())
        min_amt = _MIN_AMOUNT[cur]
        sym = _CURRENCY_SYMBOL[cur]
        state_map = {
            "RUB": DonationPaymentStates.waiting_custom_rub_amount,
            "USD": DonationPaymentStates.waiting_custom_usd_amount,
            "EUR": DonationPaymentStates.waiting_custom_eur_amount,
        }
        await state.set_state(state_map[cur])
        await callback.message.edit_text(
            f"💎 **Введите сумму в {cur} (минимум {min_amt} {sym}):**",
            reply_markup=self._cancel_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )
        await callback.answer()

    async def on_custom_amount_message(self, message: Message, state: FSMContext) -> None:
        try:
            data = await state.get_data()
            currency = (data.get("currency") or "rub").upper()
            amount = int(message.text.strip())
            min_amt = _MIN_AMOUNT.get(currency, 1)
            if amount < min_amt:
                sym = _CURRENCY_SYMBOL.get(currency, currency)
                await message.answer(
                    f"❌ Минимальная сумма {min_amt} {sym}. Введите сумму {min_amt} или больше."
                )
                return
            try:
                await message.delete()
            except Exception:
                pass
            await self._create_payment_message(message, state, currency, amount)
            await state.clear()
        except ValueError:
            await message.answer("❌ Пожалуйста, введите корректную сумму (только цифры)")
        except Exception as e:
            logger.error("❌ custom amount: %s", e, exc_info=True)
            await message.answer("❌ Произошла ошибка. Попробуйте еще раз.")

    def _user_telegram_json(self, user) -> str:
        return json.dumps(
            {
                "first_name": user.first_name,
                "last_name": user.last_name,
                "username": user.username,
                "language_code": user.language_code,
            },
            ensure_ascii=False,
        )

    def _payment_fail_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="payment_back_mode")],
                [InlineKeyboardButton(text="❌ Закрыть", callback_data="payment_cancel")],
            ]
        )

    async def _create_payment_callback(
        self,
        callback: CallbackQuery,
        state: FSMContext,
        currency: str,
        amount: int,
    ) -> None:
        await callback.answer("⏳ Создаю платеж...")
        mode = (await state.get_data()).get("donation_mode", "one_time")
        user = callback.from_user
        try:
            text, keyboard = await self._build_payment(
                user_id=user.id,
                user=user,
                currency=currency,
                amount=amount,
                mode=mode,
            )
            await callback.message.edit_text(
                text,
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
        except BZBCreatePaymentError as e:
            logger.warning("BZB payment rejected: %s", e.detail)
            await callback.message.edit_text(
                e.user_message,
                reply_markup=self._payment_fail_keyboard(),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.error("❌ payment callback: %s", e, exc_info=True)
            await callback.message.edit_text(
                "❌ Не удалось создать платеж. Попробуйте позже.",
                reply_markup=self._payment_fail_keyboard(),
                parse_mode=ParseMode.MARKDOWN,
            )

    async def _create_payment_message(
        self,
        message: Message,
        state: FSMContext,
        currency: str,
        amount: int,
    ) -> None:
        mode = (await state.get_data()).get("donation_mode", "one_time")
        user = message.from_user
        try:
            text, keyboard = await self._build_payment(
                user_id=user.id,
                user=user,
                currency=currency,
                amount=amount,
                mode=mode,
            )
            await message.answer(
                text,
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
        except BZBCreatePaymentError as e:
            logger.warning("BZB payment rejected: %s", e.detail)
            await message.answer(
                e.user_message,
                reply_markup=self._payment_fail_keyboard(),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.error("❌ payment message: %s", e, exc_info=True)
            await message.answer(
                "❌ Не удалось создать платеж. Попробуйте позже.",
                reply_markup=self._payment_fail_keyboard(),
                parse_mode=ParseMode.MARKDOWN,
            )

    async def _build_payment(
        self,
        *,
        user_id: int,
        user,
        currency: str,
        amount: int,
        mode: str,
    ) -> tuple[str, InlineKeyboardMarkup]:
        cur = currency.upper()
        is_monthly = mode == "monthly" and self._recurring_enabled()
        bot_username = await resolve_telegram_bot_username(self.bot)
        if not bot_username:
            raise RuntimeError("TELEGRAM_BOT_USERNAME not set")

        if is_monthly:
            if not self.bzb_service:
                raise RuntimeError("BZB not configured")
            service = self.bzb_service
            provider = "bzb"
            payment_type = "subscription"
            description = f"Monthly support {amount} {cur}"
            title = f"Ежемесячная поддержка {amount} {cur}"
            create_kwargs = {
                "currency": cur,
                "title": title,
            }
        else:
            service, provider = resolve_donation_payment_service(
                cur,
                yookassa_service=self.yookassa_service,
                bzb_service=self.bzb_service,
            )
            payment_type = "one_time"
            description = f"Donation {amount} {cur}"
            create_kwargs: dict = {}
            if provider == "bzb":
                create_kwargs["currency"] = cur
            elif provider == "yookassa":
                create_kwargs["save_payment_method"] = False

        confirmation_url, provider_pid, _meta = await service.create_payment(
            amount=float(amount),
            description=description,
            user_id=user_id,
            payment_type=payment_type,
            bot_username=bot_username,
            **create_kwargs,
        )
        if not confirmation_url or not provider_pid:
            raise RuntimeError("empty provider response")

        row_id = await self.user_storage.create_payment(
            user_id=user_id,
            amount=float(amount),
            payment_type=payment_type,
            provider=provider,
            provider_payment_id=provider_pid,
            user_telegram_data=self._user_telegram_json(user),
            currency=cur,
            order_id=None,
            provider_checkout_url=confirmation_url,
        )
        if not row_id:
            raise RuntimeError("create_payment failed")

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Оплатить", url=confirmation_url)],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="payment_cancel")],
            ]
        )
        if is_monthly:
            text = (
                f"📅 **Ежемесячная поддержка:** {amount} {cur} / мес\n\n"
                "Для оформления подписки перейдите по ссылке ниже:"
            )
        else:
            text = (
                f"💰 **Сумма:** {amount} {cur}\n\n"
                "Для оплаты перейдите по ссылке ниже:"
            )
        logger.info(
            "💰 donation payment row=%s provider=%s type=%s user=%s",
            row_id,
            provider,
            payment_type,
            user_id,
        )
        return text, keyboard

    async def _show_my_subscription(self, callback: CallbackQuery) -> None:
        uid = callback.from_user.id if callback.from_user else 0
        sub = await self.user_storage.get_user_active_donation_subscription(uid)
        if not sub:
            await callback.answer("Активная подписка не найдена", show_alert=True)
            return
        cur = (sub.get("currency") or "RUB").upper()
        sym = _CURRENCY_SYMBOL.get(cur, cur)
        status = sub.get("status") or "?"
        next_at = sub.get("next_charge_at")
        next_line = ""
        if next_at:
            if hasattr(next_at, "strftime"):
                next_line = f"\n📆 Следующее списание: {next_at.strftime('%d.%m.%Y')}"
            else:
                next_line = f"\n📆 Следующее списание: {next_at}"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Отменить подписку",
                        callback_data="payment_cancel_subscription",
                    )
                ],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="payment_back_mode")],
            ]
        )
        await callback.message.edit_text(
            f"📋 **Ваша подписка**\n\n"
            f"💰 {sub.get('amount')} {sym} / мес\n"
            f"📌 Статус: {status}"
            f"{next_line}",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN,
        )
        await callback.answer()

    async def _confirm_cancel_subscription(self, callback: CallbackQuery) -> None:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Да, отменить",
                        callback_data="payment_confirm_cancel_sub",
                    )
                ],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="payment_my_subscription")],
            ]
        )
        await callback.message.edit_text(
            "Вы уверены, что хотите отменить ежемесячную поддержку?\n"
            "Новые списания производиться не будут.",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN,
        )
        await callback.answer()

    async def _do_cancel_subscription(self, callback: CallbackQuery) -> None:
        uid = callback.from_user.id if callback.from_user else 0
        sub = await self.user_storage.get_user_active_donation_subscription(uid)
        if not sub:
            await callback.answer("Подписка не найдена", show_alert=True)
            return
        if not self.bzb_service:
            await callback.answer("Сервис недоступен", show_alert=True)
            return
        bzb_id = sub["bzb_subscription_id"]
        result = await self.bzb_service.cancel_subscription(bzb_id)
        if not result:
            await callback.message.edit_text(
                "❌ Не удалось отменить подписку. Попробуйте позже или напишите в поддержку.",
                parse_mode=ParseMode.MARKDOWN,
            )
            await callback.answer()
            return
        await self.user_storage.mark_donation_subscription_canceled(int(sub["id"]))
        await callback.message.edit_text(
            "✅ Подписка отменена. Спасибо, что поддерживали проект!",
            parse_mode=ParseMode.MARKDOWN,
        )
        await callback.answer("Подписка отменена")

    async def _handle_crypto_donation(self, callback: CallbackQuery) -> None:
        address = (
            os.getenv("BIBLIA_CRYPTO_USDT_TRON_ADDRESS", "").strip()
            or "TTq5YQ8NHowe9zT4bqW7gW79kDeioFCnpu"
        )
        msg = (
            "💎 **Донат криптовалютой**\n\n"
            "Вы можете поддержать проект, отправив средства на следующий адрес:\n\n"
            f"`{address}`\n\n"
            "📌 **Сеть:** TRC-20 (Tron)\n"
            "💡 **Важно:** Убедитесь, что используете правильную сеть для перевода.\n\n"
            "Спасибо за вашу поддержку! ❤️"
        )
        await callback.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN)
        await callback.answer("✅ Адрес для перевода")

    async def _back_to_mode(self, callback: CallbackQuery, state: FSMContext) -> None:
        uid = callback.from_user.id if callback.from_user else 0
        active_sub = await self.user_storage.get_user_active_donation_subscription(uid)

        if not self._recurring_enabled():
            await state.update_data(donation_mode="one_time")
            await state.set_state(None)
            await callback.message.edit_text(
                self._donation_intro_text() + "Выберите валюту для доната:",
                reply_markup=self._currency_keyboard(
                    include_crypto=True,
                    show_subscription_mgmt=bool(active_sub),
                    show_back=False,
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
            await callback.answer()
            return

        await state.clear()
        await callback.message.edit_text(
            self._donation_intro_text() + "Выберите формат поддержки:",
            reply_markup=self._mode_keyboard(show_subscription_mgmt=bool(active_sub)),
            parse_mode=ParseMode.MARKDOWN,
        )
        await callback.answer()

    async def _back_to_currency(self, callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        mode = data.get("donation_mode", "one_time")
        include_crypto = mode != "monthly"
        uid = callback.from_user.id if callback.from_user else 0
        active_sub = await self.user_storage.get_user_active_donation_subscription(uid)
        if mode == "monthly":
            title = "📅 **Ежемесячная поддержка**\n\nВыберите валюту:"
        elif self._recurring_enabled():
            title = "💳 **Разовый платёж**\n\nВыберите валюту:"
        else:
            title = self._donation_intro_text() + "Выберите валюту для доната:"
        await callback.message.edit_text(
            title,
            reply_markup=self._currency_keyboard(
                include_crypto=include_crypto,
                show_subscription_mgmt=bool(active_sub) and not self._recurring_enabled(),
                show_back=self._recurring_enabled(),
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
        await callback.answer()

    async def _cancel(self, callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await callback.message.delete()
        await callback.answer("❌ Операция отменена")
