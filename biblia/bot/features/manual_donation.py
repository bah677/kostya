"""Админ: универсальный ручной учёт доната (/manual_donat, /marathon_crypto)."""

from __future__ import annotations

import html
import logging
from typing import Any, Dict, Optional

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
from bot.payments.currency_converter import CurrencyConverterService
from bot.services.donation_marathon_progress import format_money
from bot.services.manual_donation import (
    POOL_PERIOD_CURRENT,
    POOL_PERIOD_NEXT,
    record_manual_donation,
)
from bot.services.prayer_voice_quota import format_reset_hint, quota_day_for

logger = logging.getLogger(__name__)

_CB = "mdon_"
_FALLBACK_CURRENCIES = ("RUB", "USD", "EUR", "USDT")
_EXTRA_MANUAL_PROVIDERS = ("crypto", "bank", "cash")
_PROVIDER_LABELS = {
    "crypto": "Крипта",
    "bank": "Банк/перевод",
    "cash": "Наличные",
    "yookassa": "YooKassa",
    "bzb": "BZB",
    "manual": "manual",
}


class ManualDonationStates(StatesGroup):
    amount = State()
    currency = State()
    currency_other = State()
    provider = State()
    provider_other = State()
    user_id = State()
    note = State()
    options = State()
    pool_period = State()
    confirm = State()


class ManualDonationFeature(BaseFeature):
    name = "manual_donation"

    def __init__(self, user_storage, bot=None) -> None:
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot

    def set_bot(self, telegram_app) -> None:
        self.bot = telegram_app.bot if telegram_app else self.bot

    def register_handlers(self, dp: Dispatcher) -> None:
        private = F.chat.type == ChatType.PRIVATE
        dp.message.register(
            self.cmd_start, private, Command("manual_donat")
        )
        dp.message.register(
            self.cmd_start, private, Command("donat_manual")
        )
        # Старый вход — тот же мастер (марафон+крипта по умолчанию).
        dp.message.register(
            self.cmd_start_crypto_alias, private, Command("marathon_crypto")
        )

        dp.message.register(
            self._on_amount, StateFilter(ManualDonationStates.amount), F.text
        )
        dp.message.register(
            self._on_currency_other,
            StateFilter(ManualDonationStates.currency_other),
            F.text,
        )
        dp.message.register(
            self._on_provider_other,
            StateFilter(ManualDonationStates.provider_other),
            F.text,
        )
        dp.message.register(
            self._on_user_id, StateFilter(ManualDonationStates.user_id), F.text
        )
        dp.message.register(
            self._on_note, StateFilter(ManualDonationStates.note), F.text
        )

        dp.callback_query.register(
            self.on_callback, F.data.startswith(_CB)
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

    async def cmd_start(self, message: Message, state: FSMContext) -> None:
        await self._begin(
            message,
            state,
            prefer_marathon=False,
            prefer_provider=None,
        )

    async def cmd_start_crypto_alias(
        self, message: Message, state: FSMContext
    ) -> None:
        await self._begin(
            message,
            state,
            prefer_marathon=True,
            prefer_provider="crypto",
        )

    async def _begin(
        self,
        message: Message,
        state: FSMContext,
        *,
        prefer_marathon: bool,
        prefer_provider: Optional[str],
    ) -> None:
        if not await self._ensure_admin(message):
            return
        await self.user_storage.ensure_prayer_voice_quota_schema()
        await state.clear()
        active = await self.user_storage.get_active_donation_marathon()
        await state.set_state(ManualDonationStates.amount)
        await state.update_data(
            count_marathon=bool(prefer_marathon and active),
            count_pool=True,
            pool_period=POOL_PERIOD_NEXT,
            provider=prefer_provider,
            marathon_available=bool(active),
            marathon_name=(active or {}).get("name"),
        )
        hint = ""
        if prefer_provider == "crypto":
            hint = (
                "\n<i>Алиас /marathon_crypto: крипта + марафон (если активен).</i>\n"
            )
        await message.answer(
            "✍️ <b>Ручной донат</b>\n"
            f"{hint}\n"
            "Введите сумму (число), например <code>25</code> или <code>1500.50</code>:\n"
            "Отмена: /cancel",
            parse_mode=ParseMode.HTML,
        )

    async def _on_amount(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message):
            return
        raw = (message.text or "").strip().replace(",", ".")
        if raw.lower() in ("/cancel", "cancel", "отмена"):
            await state.clear()
            await message.answer("Отменено.")
            return
        try:
            amount = float(raw)
        except ValueError:
            await message.answer("Нужно число, например <code>25</code>.", parse_mode=ParseMode.HTML)
            return
        if amount <= 0:
            await message.answer("Сумма должна быть > 0.")
            return
        await state.update_data(amount=amount)
        await state.set_state(ManualDonationStates.currency)
        await message.answer(
            "Валюта (из платежей или свой вариант):",
            reply_markup=await self._currency_kb(),
        )

    async def _currency_choices(self) -> list[str]:
        existing = await self.user_storage.list_distinct_payment_currencies(limit=10)
        seen = set()
        out: list[str] = []
        for c in list(existing) + list(_FALLBACK_CURRENCIES):
            cu = (c or "").strip().upper()
            if not cu or cu in seen:
                continue
            seen.add(cu)
            out.append(cu)
        return out[:12]

    async def _provider_choices(self) -> list[str]:
        existing = await self.user_storage.list_distinct_payment_providers(limit=10)
        seen = set()
        out: list[str] = []
        for p in list(existing) + list(_EXTRA_MANUAL_PROVIDERS):
            pl = (p or "").strip().lower()
            if not pl or pl in seen:
                continue
            seen.add(pl)
            out.append(pl)
        return out[:12]

    async def _currency_kb(self) -> InlineKeyboardMarkup:
        currencies = await self._currency_choices()
        rows: list[list[InlineKeyboardButton]] = []
        pair: list[InlineKeyboardButton] = []
        for c in currencies:
            pair.append(
                InlineKeyboardButton(text=c, callback_data=f"{_CB}cur_{c}")
            )
            if len(pair) == 2:
                rows.append(pair)
                pair = []
        if pair:
            rows.append(pair)
        rows.append(
            [
                InlineKeyboardButton(
                    text="Свой вариант…", callback_data=f"{_CB}cur_OTHER"
                )
            ]
        )
        rows.append(
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{_CB}cancel")]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def _provider_kb(self) -> InlineKeyboardMarkup:
        providers = await self._provider_choices()
        rows: list[list[InlineKeyboardButton]] = []
        pair: list[InlineKeyboardButton] = []
        for code in providers:
            label = _PROVIDER_LABELS.get(code, code)
            pair.append(
                InlineKeyboardButton(text=label, callback_data=f"{_CB}prv_{code}")
            )
            if len(pair) == 2:
                rows.append(pair)
                pair = []
        if pair:
            rows.append(pair)
        rows.append(
            [
                InlineKeyboardButton(
                    text="Свой вариант…", callback_data=f"{_CB}prv_other"
                )
            ]
        )
        rows.append(
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{_CB}cancel")]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def _options_kb(self, data: Dict[str, Any]) -> InlineKeyboardMarkup:
        def mark(on: bool, label: str) -> str:
            return f"{'✅' if on else '⬜'} {label}"

        marathon_avail = bool(data.get("marathon_available"))
        rows = []
        if marathon_avail:
            name = html.escape(str(data.get("marathon_name") or "марафон")[:40])
            rows.append(
                [
                    InlineKeyboardButton(
                        text=mark(bool(data.get("count_marathon")), f"Марафон «{name}»"),
                        callback_data=f"{_CB}tog_marathon",
                    )
                ]
            )
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="⬜ Марафон (нет активного)",
                        callback_data=f"{_CB}noop",
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text=mark(bool(data.get("count_pool")), "Пул голоса молитв"),
                    callback_data=f"{_CB}tog_pool",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(text="➡️ Далее", callback_data=f"{_CB}opts_next"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"{_CB}cancel"),
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _pool_period_kb(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Текущий период (сейчас в пуле)",
                        callback_data=f"{_CB}pool_{POOL_PERIOD_CURRENT}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Следующий (с 08:00, индикатив)",
                        callback_data=f"{_CB}pool_{POOL_PERIOD_NEXT}",
                    )
                ],
                [InlineKeyboardButton(text="❌ Отмена", callback_data=f"{_CB}cancel")],
            ]
        )

    def _confirm_kb(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Записать", callback_data=f"{_CB}confirm"
                    ),
                    InlineKeyboardButton(text="❌ Отмена", callback_data=f"{_CB}cancel"),
                ]
            ]
        )

    async def on_callback(self, callback: CallbackQuery, state: FSMContext) -> None:
        uid = callback.from_user.id if callback.from_user else None
        if uid is None or not await is_telegram_admin(self.user_storage, uid):
            await callback.answer("Нет доступа", show_alert=True)
            return
        data = callback.data or ""
        if data == f"{_CB}cancel":
            await state.clear()
            await callback.message.edit_text("Отменено.") if callback.message else None
            await callback.answer()
            return
        if data == f"{_CB}noop":
            await callback.answer("Нет активного марафона")
            return

        if data.startswith(f"{_CB}cur_"):
            cur = data[len(f"{_CB}cur_") :]
            if cur == "OTHER":
                await state.set_state(ManualDonationStates.currency_other)
                await callback.message.answer(
                    "Код валюты (например <code>GBP</code>):",
                    parse_mode=ParseMode.HTML,
                )
                await callback.answer()
                return
            await state.update_data(currency=cur)
            st = await state.get_data()
            if st.get("provider"):
                await state.set_state(ManualDonationStates.user_id)
                await callback.message.answer(
                    "Telegram <b>user_id</b> донора.\n"
                    "Если неизвестен — отправьте <code>0</code>:",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await state.set_state(ManualDonationStates.provider)
                await callback.message.answer(
                    "Провайдер (из платежей или свой вариант):",
                    reply_markup=await self._provider_kb(),
                )
            await callback.answer()
            return

        if data.startswith(f"{_CB}prv_"):
            code = data[len(f"{_CB}prv_") :]
            if code == "other":
                await state.set_state(ManualDonationStates.provider_other)
                await callback.message.answer(
                    "Короткое имя провайдера латиницей (например <code>paypal</code>):",
                    parse_mode=ParseMode.HTML,
                )
                await callback.answer()
                return
            await state.update_data(provider=code)
            await state.set_state(ManualDonationStates.user_id)
            await callback.message.answer(
                "Telegram <b>user_id</b> донора.\n"
                "Если неизвестен — отправьте <code>0</code>:",
                parse_mode=ParseMode.HTML,
            )
            await callback.answer()
            return

        if data == f"{_CB}tog_marathon":
            st = await state.get_data()
            if not st.get("marathon_available"):
                await callback.answer("Нет активного марафона")
                return
            await state.update_data(count_marathon=not bool(st.get("count_marathon")))
            st = await state.get_data()
            await callback.message.edit_reply_markup(
                reply_markup=await self._options_kb(st)
            )
            await callback.answer()
            return

        if data == f"{_CB}tog_pool":
            st = await state.get_data()
            await state.update_data(count_pool=not bool(st.get("count_pool")))
            st = await state.get_data()
            await callback.message.edit_reply_markup(
                reply_markup=await self._options_kb(st)
            )
            await callback.answer()
            return

        if data == f"{_CB}opts_next":
            st = await state.get_data()
            if not st.get("count_marathon") and not st.get("count_pool"):
                await callback.answer(
                    "Выберите хотя бы марафон или пул голоса",
                    show_alert=True,
                )
                return
            if st.get("count_pool"):
                await state.set_state(ManualDonationStates.pool_period)
                day = quota_day_for()
                await callback.message.answer(
                    "Куда полонить <b>пул голоса</b>?\n\n"
                    f"• <b>Текущий</b> — сразу увеличить лимит на {day.isoformat()} "
                    f"(донат пишется в окно вчерашних 08:00→08:00, период пересчитывается).\n"
                    f"• <b>Следующий</b> — учесть в сегодняшнем окне; лимит сработает "
                    f"после сброса ({html.escape(format_reset_hint())}).",
                    parse_mode=ParseMode.HTML,
                    reply_markup=self._pool_period_kb(),
                )
            else:
                await state.update_data(pool_period=None)
                await self._ask_confirm(callback.message, state)
            await callback.answer()
            return

        if data.startswith(f"{_CB}pool_"):
            period = data[len(f"{_CB}pool_") :]
            if period not in (POOL_PERIOD_CURRENT, POOL_PERIOD_NEXT):
                await callback.answer("Неизвестный период")
                return
            await state.update_data(pool_period=period)
            await self._ask_confirm(callback.message, state)
            await callback.answer()
            return

        if data == f"{_CB}confirm":
            await self._do_confirm(callback, state)
            return

        await callback.answer()

    async def _on_currency_other(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message):
            return
        cur = (message.text or "").strip().upper()
        if not cur.isalpha() or len(cur) < 3 or len(cur) > 8:
            await message.answer("Нужен код из 3–8 букв (ISO), например GBP.")
            return
        await state.update_data(currency=cur)
        st = await state.get_data()
        if st.get("provider"):
            await state.set_state(ManualDonationStates.user_id)
            await message.answer(
                "Telegram <b>user_id</b> донора.\n"
                "Если неизвестен — отправьте <code>0</code>:",
                parse_mode=ParseMode.HTML,
            )
        else:
            await state.set_state(ManualDonationStates.provider)
            await message.answer(
                "Провайдер (из платежей или свой вариант):",
                reply_markup=await self._provider_kb(),
            )

    async def _on_provider_other(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message):
            return
        raw = (message.text or "").strip().lower().replace(" ", "_")
        if not raw or len(raw) > 32 or not all(c.isalnum() or c in "_-" for c in raw):
            await message.answer("Латиница/цифры, до 32 символов.")
            return
        await state.update_data(provider=raw)
        await state.set_state(ManualDonationStates.user_id)
        await message.answer(
            "Telegram <b>user_id</b> донора.\n"
            "Если неизвестен — отправьте <code>0</code>:",
            parse_mode=ParseMode.HTML,
        )

    async def _on_user_id(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message):
            return
        raw = (message.text or "").strip()
        try:
            user_id = int(raw)
        except ValueError:
            await message.answer("Целое число user_id или 0.")
            return
        if user_id < 0:
            await message.answer("user_id ≥ 0.")
            return
        await state.update_data(user_id=user_id)
        await state.set_state(ManualDonationStates.note)
        await message.answer("Комментарий / txid (или <code>-</code>):", parse_mode=ParseMode.HTML)

    async def _on_note(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message):
            return
        note = (message.text or "").strip()
        if note == "-":
            note = ""
        await state.update_data(note=note[:500])
        # Обновить доступность марафона на момент выбора
        active = await self.user_storage.get_active_donation_marathon()
        st = await state.get_data()
        await state.update_data(
            marathon_available=bool(active),
            marathon_name=(active or {}).get("name"),
            count_marathon=bool(st.get("count_marathon") and active),
        )
        st = await state.get_data()
        await state.set_state(ManualDonationStates.options)
        await message.answer(
            "Где учитывать донат? (можно оба)",
            reply_markup=await self._options_kb(st),
        )

    async def _ask_confirm(self, message: Message, state: FSMContext) -> None:
        st = await state.get_data()
        await state.set_state(ManualDonationStates.confirm)
        text = self._preview_html(st)
        await message.answer(
            text, parse_mode=ParseMode.HTML, reply_markup=self._confirm_kb()
        )

    def _preview_html(self, st: Dict[str, Any]) -> str:
        amount = float(st.get("amount") or 0)
        cur = str(st.get("currency") or "?")
        lines = [
            "📋 <b>Проверка ручного доната</b>\n",
            f"• Сумма: <b>{html.escape(format_money(amount, cur))}</b>",
            f"• Провайдер: <code>{html.escape(str(st.get('provider') or ''))}</code>",
            f"• user_id: <code>{int(st.get('user_id') or 0)}</code>",
            f"• Комментарий: {html.escape(st.get('note') or '—')}",
        ]
        if st.get("count_marathon"):
            lines.append(
                f"• Марафон: <b>да</b> («{html.escape(str(st.get('marathon_name') or ''))}»)"
            )
        else:
            lines.append("• Марафон: нет")
        if st.get("count_pool"):
            period = st.get("pool_period")
            label = (
                "текущий период"
                if period == POOL_PERIOD_CURRENT
                else "следующий (с 08:00)"
            )
            lines.append(f"• Пул голоса: <b>да</b> → {label}")
        else:
            lines.append("• Пул голоса: нет")
        uid = int(st.get("user_id") or 0)
        if uid > 0:
            lines.append("• Уведомления: админам + «спасибо» пользователю")
        else:
            lines.append("• Уведомления: только админам (user_id=0)")
        lines.append("\nЗаписать?")
        return "\n".join(lines)

    async def _do_confirm(self, callback: CallbackQuery, state: FSMContext) -> None:
        st = await state.get_data()
        if not st.get("amount") or not st.get("currency") or not st.get("provider"):
            await callback.answer("Данные потеряны — начните /manual_donat", show_alert=True)
            await state.clear()
            return
        if not st.get("count_marathon") and not st.get("count_pool"):
            await callback.answer("Нечего учитывать", show_alert=True)
            return

        admin_id = callback.from_user.id if callback.from_user else 0
        wait = await callback.message.answer("⏳ Записываю…")
        await callback.answer()
        try:
            result = await record_manual_donation(
                self.user_storage,
                amount=float(st["amount"]),
                currency=str(st["currency"]),
                provider=str(st["provider"]),
                user_id=int(st.get("user_id") or 0),
                note=str(st.get("note") or ""),
                created_by=int(admin_id),
                count_in_marathon=bool(st.get("count_marathon")),
                count_in_voice_pool=bool(st.get("count_pool")),
                pool_period=st.get("pool_period"),
                currency_converter=CurrencyConverterService(),
            )
        except Exception as e:
            logger.exception("manual donation failed: %s", e)
            await wait.edit_text(f"❌ {html.escape(str(e)[:300])}")
            await state.clear()
            return

        await state.clear()
        p = result.payment
        rub = float(p.get("amount_rub") or 0)
        donor_uid = int(p.get("user_id") or 0)
        parts = [
            f"✅ Донат записан: payment <code>{p.get('id')}</code>",
            f"{html.escape(format_money(float(p.get('amount') or 0), str(p.get('currency'))))} "
            f"→ {rub:.2f} ₽",
            f"провайдер <code>{html.escape(str(p.get('payment_provider')))}</code>",
        ]
        if result.marathon_contribution:
            c = result.marathon_contribution
            parts.append(
                "Марафон: +"
                + format_money(
                    float(c.get("amount_goal") or 0),
                    str(c.get("goal_currency") or ""),
                )
            )
            if result.marathon_closed:
                parts.append("🏁 Цель марафона достигнута — закрыт.")
        if result.period_info:
            info = result.period_info
            parts.append(
                f"Пул сейчас ({info['quota_day']}): "
                f"лимит {info['limit_slots']} "
                f"(used {info['used']}, осталось {info['remaining']})"
            )
        elif result.next_slots is not None:
            parts.append(f"Индикатив лимита на завтра: {result.next_slots}")

        notify_bits: list[str] = []
        bot = self.bot
        if bot:
            try:
                from bot.payments.standalone_donation_notify import (
                    notify_admins_standalone_donation_success,
                )

                await notify_admins_standalone_donation_success(
                    bot,
                    self.user_storage,
                    p,
                    rub_amount=rub,
                    kind="manual",
                )
                notify_bits.append("админам ✓")
            except Exception:
                logger.exception("manual donation admin notify failed")
                notify_bits.append("админам ✗")

            if donor_uid > 0:
                try:
                    thank = await self._thank_html_for_manual(result)
                    await bot.send_message(donor_uid, thank, parse_mode=ParseMode.HTML)
                    notify_bits.append("юзеру ✓")
                except Exception as e:
                    logger.warning("manual donation user thank %s: %s", donor_uid, e)
                    notify_bits.append("юзеру ✗")
            else:
                notify_bits.append("юзеру пропуск (id=0)")
        if notify_bits:
            parts.append("Уведомления: " + ", ".join(notify_bits))

        await wait.edit_text("\n".join(parts), parse_mode=ParseMode.HTML)

        if result.marathon_closed:
            try:
                from bot.services.donation_marathon_close import handle_marathon_closed

                mid = int((result.marathon_contribution or {}).get("marathon_id") or 0)
                if mid and self.bot:
                    await handle_marathon_closed(
                        self.bot,
                        self.user_storage,
                        mid,
                        create_thanks=True,
                    )
            except Exception:
                logger.exception("marathon close notify after manual donation")

    async def _thank_html_for_manual(self, result) -> str:
        thank = (
            "🙏 <b>Спасибо за поддержку проекта!</b>\n\n"
            "Ваше пожертвование получено — пусть оно вернётся к вам сторицей."
        )
        if result.marathon_contribution:
            try:
                mid = int(result.marathon_contribution.get("marathon_id") or 0)
                marathon = await self.user_storage.get_donation_marathon(mid)
                if marathon:
                    from bot.services.donation_marathon_progress import (
                        thank_you_remaining_html,
                    )

                    raised = await self.user_storage.get_marathon_raised_amount(mid)
                    thank = thank_you_remaining_html(marathon, raised=raised)
            except Exception:
                logger.debug("manual marathon thank fallback", exc_info=True)
        try:
            from bot.services.prayer_voice_funding import PrayerVoiceFundingService

            next_slots = await PrayerVoiceFundingService(
                self.user_storage, CurrencyConverterService()
            ).indicative_next_slots()
            thank = (
                f"{thank.rstrip()}\n\n"
                f"📈 Лимит бесплатного голоса молитв на завтра (сейчас): "
                f"<b>{int(next_slots)}</b>."
            )
        except Exception:
            logger.debug("manual thank tomorrow limit", exc_info=True)
        return thank
