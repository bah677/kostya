"""Админ-меню учёта депозита и расходов (USDT, задел под мультивалютность)."""

from __future__ import annotations

import html
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from aiogram import Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.admin_guard import is_admin_or_super
from bot.features.base import BaseFeature
from bot.filters.private_only import CALLBACK_PRIVATE_CHAT, PRIVATE_CHAT
from bot.services.ledger import (
    DEFAULT_CURRENCY,
    format_money,
    parse_amount,
)

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

_CB = "ldg"


class LedgerStates(StatesGroup):
    deposit_gross = State()
    deposit_fee = State()
    deposit_note = State()
    expense_custom = State()
    expense_gross = State()
    expense_fee = State()
    expense_note = State()
    confirm = State()


def _cb(action: str, value: str = "-") -> str:
    return f"{_CB}:{action}:{value}"


class LedgerFeature(BaseFeature):
    name = "ledger"

    def __init__(self, user_storage) -> None:
        super().__init__()
        self.user_storage = user_storage

    async def initialize(self) -> None:
        await self.user_storage.ensure_ledger_schema()
        logger.info("[%s] схема журнала проверена", self.name)

    def register_handlers(self, dp: Dispatcher) -> None:
        dp.message.register(
            self.cmd_expenses, PRIVATE_CHAT, Command("expenses")
        )
        dp.message.register(
            self.cmd_expenses, PRIVATE_CHAT, Command("rashody")
        )
        dp.message.register(
            self.cmd_cancel,
            PRIVATE_CHAT,
            Command("cancel"),
            StateFilter(LedgerStates),
        )
        dp.message.register(
            self._on_deposit_gross,
            PRIVATE_CHAT,
            StateFilter(LedgerStates.deposit_gross),
            F.text,
        )
        dp.message.register(
            self._on_deposit_fee,
            PRIVATE_CHAT,
            StateFilter(LedgerStates.deposit_fee),
            F.text,
        )
        dp.message.register(
            self._on_deposit_note,
            PRIVATE_CHAT,
            StateFilter(LedgerStates.deposit_note),
            F.text,
        )
        dp.message.register(
            self._on_expense_custom,
            PRIVATE_CHAT,
            StateFilter(LedgerStates.expense_custom),
            F.text,
        )
        dp.message.register(
            self._on_expense_gross,
            PRIVATE_CHAT,
            StateFilter(LedgerStates.expense_gross),
            F.text,
        )
        dp.message.register(
            self._on_expense_fee,
            PRIVATE_CHAT,
            StateFilter(LedgerStates.expense_fee),
            F.text,
        )
        dp.message.register(
            self._on_expense_note,
            PRIVATE_CHAT,
            StateFilter(LedgerStates.expense_note),
            F.text,
        )
        dp.message.register(
            self._on_unexpected,
            PRIVATE_CHAT,
            StateFilter(LedgerStates),
        )
        dp.callback_query.register(
            self.on_callback,
            CALLBACK_PRIVATE_CHAT,
            F.data.startswith(f"{_CB}:"),
        )

    async def _ensure_admin(self, uid: Optional[int]) -> bool:
        if uid is None:
            return False
        return await is_admin_or_super(self.user_storage, uid)

    async def cmd_expenses(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message.from_user.id if message.from_user else None):
            return
        await state.clear()
        await self.user_storage.ensure_ledger_schema()
        text, kb = await self._menu()
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    async def cmd_cancel(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Отменено.")
        text, kb = await self._menu()
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    async def _on_unexpected(self, message: Message, state: FSMContext) -> None:
        await message.answer("Нажмите «Записать» или «Отмена» (/cancel).")

    async def on_callback(self, callback: CallbackQuery, state: FSMContext) -> None:
        uid = callback.from_user.id if callback.from_user else None
        if not await self._ensure_admin(uid):
            await callback.answer("Нет доступа", show_alert=True)
            return
        parts = (callback.data or "").split(":", 2)
        action = parts[1] if len(parts) > 1 else ""
        value = parts[2] if len(parts) > 2 else "-"

        if action == "home":
            await state.clear()
            text, kb = await self._menu()
            await self._edit_or_answer(callback, text, kb)
            await callback.answer()
            return
        if action == "report":
            text, kb = await self._report()
            await self._edit_or_answer(callback, text, kb)
            await callback.answer()
            return
        if action == "dep":
            await state.clear()
            await state.set_state(LedgerStates.deposit_gross)
            await state.update_data(kind="deposit")
            await self._edit_or_answer(
                callback,
                "📥 <b>Пополнение депозита</b> (USDT)\n\n"
                "Введите сумму <b>брутто</b> (сколько отправили):\n"
                "Отмена: /cancel",
                self._cancel_kb(),
            )
            await callback.answer()
            return
        if action == "exp":
            await state.clear()
            cats = await self.user_storage.list_ledger_categories()
            await state.update_data(kind="expense")
            await self._edit_or_answer(
                callback,
                "📤 <b>Расход</b> (USDT)\n\nВыберите статью или введите свою:",
                self._categories_kb(cats),
            )
            await callback.answer()
            return
        if action == "cat":
            if value == "new":
                await state.set_state(LedgerStates.expense_custom)
                await self._edit_or_answer(
                    callback,
                    "Название новой статьи (до 80 символов):",
                    self._cancel_kb(),
                )
                await callback.answer()
                return
            try:
                cat_id = int(value)
            except ValueError:
                await callback.answer("Статья?")
                return
            cats = await self.user_storage.list_ledger_categories()
            found = next((c for c in cats if int(c["id"]) == cat_id), None)
            if not found:
                await callback.answer("Нет такой статьи", show_alert=True)
                return
            await state.update_data(
                category_id=int(found["id"]),
                category_name=str(found["name"]),
            )
            await state.set_state(LedgerStates.expense_gross)
            await self._edit_or_answer(
                callback,
                f"Статья: <b>{html.escape(str(found['name']))}</b>\n\n"
                "Сумма <b>брутто</b>:",
                self._cancel_kb(),
            )
            await callback.answer()
            return
        if action == "ok":
            await self._commit(callback, state)
            return
        await callback.answer()

    async def _on_deposit_gross(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message.from_user.id if message.from_user else None):
            return
        amount = parse_amount(message.text or "")
        if amount is None or amount <= 0:
            await message.answer(
                "Нужно число больше 0, например <code>50</code> или <code>12.5</code>.",
                parse_mode=ParseMode.HTML,
            )
            return
        await state.update_data(gross=str(amount))
        await state.set_state(LedgerStates.deposit_fee)
        await message.answer(
            "Сумма <b>комиссии</b> (0, если без комиссии):",
            parse_mode=ParseMode.HTML,
            reply_markup=self._cancel_kb(),
        )

    async def _on_deposit_fee(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message.from_user.id if message.from_user else None):
            return
        fee = parse_amount(message.text or "")
        if fee is None or fee < 0:
            await message.answer("Комиссия — число ≥ 0.")
            return
        data = await state.get_data()
        gross = Decimal(str(data.get("gross") or 0))
        if fee > gross:
            await message.answer("Комиссия не может быть больше брутто.")
            return
        await state.update_data(fee=str(fee))
        await state.set_state(LedgerStates.deposit_note)
        await message.answer(
            "Комментарий (или <code>-</code>):",
            parse_mode=ParseMode.HTML,
            reply_markup=self._cancel_kb(),
        )

    async def _on_deposit_note(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message.from_user.id if message.from_user else None):
            return
        note = (message.text or "").strip()
        if note == "-":
            note = ""
        await state.update_data(note=note[:500])
        await self._ask_confirm(message, state)

    async def _on_expense_custom(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message.from_user.id if message.from_user else None):
            return
        name = (message.text or "").strip()
        if len(name) < 2 or len(name) > 80:
            await message.answer("Название 2–80 символов.")
            return
        cat = await self.user_storage.upsert_ledger_category(name)
        if not cat:
            await message.answer("Не удалось сохранить статью.")
            return
        await state.update_data(
            category_id=int(cat["id"]),
            category_name=str(cat["name"]),
        )
        await state.set_state(LedgerStates.expense_gross)
        await message.answer(
            f"Статья: <b>{html.escape(str(cat['name']))}</b>\n\nСумма <b>брутто</b>:",
            parse_mode=ParseMode.HTML,
            reply_markup=self._cancel_kb(),
        )

    async def _on_expense_gross(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message.from_user.id if message.from_user else None):
            return
        data = await state.get_data()
        if not data.get("category_id"):
            await message.answer("Сначала выберите статью кнопкой.")
            return
        amount = parse_amount(message.text or "")
        if amount is None or amount <= 0:
            await message.answer(
                "Нужно число больше 0.",
                parse_mode=ParseMode.HTML,
            )
            return
        await state.update_data(gross=str(amount))
        await state.set_state(LedgerStates.expense_fee)
        await message.answer(
            "Сумма <b>комиссии</b> (0, если без комиссии):",
            parse_mode=ParseMode.HTML,
            reply_markup=self._cancel_kb(),
        )

    async def _on_expense_fee(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message.from_user.id if message.from_user else None):
            return
        fee = parse_amount(message.text or "")
        if fee is None or fee < 0:
            await message.answer("Комиссия — число ≥ 0.")
            return
        await state.update_data(fee=str(fee))
        await state.set_state(LedgerStates.expense_note)
        await message.answer(
            "Комментарий (или <code>-</code>):",
            parse_mode=ParseMode.HTML,
            reply_markup=self._cancel_kb(),
        )

    async def _on_expense_note(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message.from_user.id if message.from_user else None):
            return
        note = (message.text or "").strip()
        if note == "-":
            note = ""
        await state.update_data(note=note[:500])
        await self._ask_confirm(message, state)

    async def _ask_confirm(self, message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        acc = await self.user_storage.get_default_ledger_account()
        text = self._preview_html(data, acc)
        await state.set_state(LedgerStates.confirm)
        await message.answer(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Записать", callback_data=_cb("ok")),
                        InlineKeyboardButton(text="❌ Отмена", callback_data=_cb("home")),
                    ]
                ]
            ),
        )

    def _preview_html(self, data: Dict[str, Any], acc: Optional[Dict[str, Any]]) -> str:
        kind = str(data.get("kind") or "")
        gross = Decimal(str(data.get("gross") or 0))
        fee = Decimal(str(data.get("fee") or 0))
        cur = DEFAULT_CURRENCY
        balance = Decimal(str((acc or {}).get("balance") or 0))
        lines = ["📋 <b>Проверка</b>\n"]
        if kind == "deposit":
            net = gross - fee
            after = balance + net
            lines += [
                "📥 Пополнение депозита",
                f"• Брутто: <b>{html.escape(format_money(gross, cur))}</b>",
                f"• Комиссия: <b>{html.escape(format_money(fee, cur))}</b>",
                f"• Нетто на депозит: <b>{html.escape(format_money(net, cur))}</b>",
            ]
        else:
            net = gross - fee
            cash = gross + fee
            after = balance - cash
            lines += [
                "📤 Расход",
                f"• Статья: <b>{html.escape(str(data.get('category_name') or '—'))}</b>",
                f"• Брутто: <b>{html.escape(format_money(gross, cur))}</b>",
                f"• Комиссия: <b>{html.escape(format_money(fee, cur))}</b>",
                f"• Нетто (брутто − комиссия): <b>{html.escape(format_money(net, cur))}</b>",
                f"• Списание с депозита (брутто + комиссия): <b>{html.escape(format_money(cash, cur))}</b>",
            ]
        note = str(data.get("note") or "").strip()
        lines.append(f"• Комментарий: {html.escape(note or '—')}")
        lines.append(f"• Остаток сейчас: {html.escape(format_money(balance, cur))}")
        lines.append(f"• Остаток после: <b>{html.escape(format_money(after, cur))}</b>")
        lines.append("\nЗаписать?")
        return "\n".join(lines)

    async def _commit(self, callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        kind = str(data.get("kind") or "")
        try:
            gross = Decimal(str(data.get("gross") or 0))
            fee = Decimal(str(data.get("fee") or 0))
        except Exception:
            await callback.answer("Данные потеряны — /expenses", show_alert=True)
            await state.clear()
            return
        if kind not in ("deposit", "expense") or gross <= 0:
            await callback.answer("Данные потеряны — /expenses", show_alert=True)
            await state.clear()
            return
        uid = callback.from_user.id if callback.from_user else 0
        row = await self.user_storage.add_ledger_entry(
            kind=kind,
            gross=gross,
            fee=fee,
            created_by=int(uid),
            category_id=int(data["category_id"]) if data.get("category_id") else None,
            category_name=str(data.get("category_name") or "") or None,
            note=str(data.get("note") or "") or None,
        )
        await state.clear()
        if not row:
            await callback.message.answer("❌ Не удалось записать.")
            await callback.answer()
            return
        cur = str(row.get("currency") or DEFAULT_CURRENCY)
        after = format_money(row.get("balance_after"), cur)
        if kind == "deposit":
            msg = (
                f"✅ Депозит +{html.escape(format_money(row['amount_net'], cur))}\n"
                f"брутто {html.escape(format_money(row['amount_gross'], cur))}, "
                f"комиссия {html.escape(format_money(row['amount_fee'], cur))}\n"
                f"Остаток: <b>{html.escape(after)}</b>"
            )
        else:
            cash = Decimal(str(row["amount_gross"])) + Decimal(str(row["amount_fee"]))
            msg = (
                f"✅ Расход «{html.escape(str(row.get('category_name') or ''))}»\n"
                f"брутто {html.escape(format_money(row['amount_gross'], cur))}, "
                f"нетто {html.escape(format_money(row['amount_net'], cur))}, "
                f"списано {html.escape(format_money(cash, cur))}\n"
                f"Остаток: <b>{html.escape(after)}</b>"
            )
        await callback.message.edit_text(msg, parse_mode=ParseMode.HTML)
        text, kb = await self._menu()
        await callback.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        await callback.answer("Записано")

    async def _menu(self) -> tuple[str, InlineKeyboardMarkup]:
        acc = await self.user_storage.get_default_ledger_account()
        cur = str((acc or {}).get("currency") or DEFAULT_CURRENCY)
        bal = format_money((acc or {}).get("balance") or 0, cur)
        recent = await self.user_storage.list_recent_ledger_entries(
            limit=5, account_id=int(acc["id"]) if acc else None
        )
        lines = [
            "💸 <b>Расходы</b>",
            f"Счёт: <b>{html.escape(str((acc or {}).get('name') or 'USDT'))}</b>",
            f"Остаток: <b>{html.escape(bal)}</b>",
            "",
        ]
        if recent:
            lines.append("Последние операции:")
            for e in recent:
                lines.append(self._entry_line(e))
            lines.append("")
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📥 Пополнить депозит", callback_data=_cb("dep"))],
                [InlineKeyboardButton(text="📤 Расход", callback_data=_cb("exp"))],
                [InlineKeyboardButton(text="📊 Отчёт", callback_data=_cb("report"))],
            ]
        )
        return "\n".join(lines), kb

    async def _report(self) -> tuple[str, InlineKeyboardMarkup]:
        acc = await self.user_storage.get_default_ledger_account()
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="« Меню", callback_data=_cb("home"))]
            ]
        )
        if not acc:
            return "Счёт ещё не создан.", kb
        cur = str(acc.get("currency") or DEFAULT_CURRENCY)
        now = datetime.now(MSK)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month = await self.user_storage.ledger_totals(
            account_id=int(acc["id"]), since=month_start
        )
        all_t = await self.user_storage.ledger_totals(account_id=int(acc["id"]))
        text = (
            "📊 <b>Отчёт</b>\n"
            f"Остаток: <b>{html.escape(format_money(acc.get('balance'), cur))}</b>\n\n"
            f"<b>Этот месяц</b> ({month_start.strftime('%m.%Y')})\n"
            f"{self._totals_html(month, cur)}\n"
            f"<b>Всё время</b>\n"
            f"{self._totals_html(all_t, cur)}"
        )
        return text, kb

    @staticmethod
    def _totals_html(t: Dict[str, Decimal], cur: str) -> str:
        return (
            f"• Депозит брутто: {html.escape(format_money(t['deposit_gross'], cur))}\n"
            f"• Депозит комиссия: {html.escape(format_money(t['deposit_fee'], cur))}\n"
            f"• Депозит нетто: {html.escape(format_money(t['deposit_net'], cur))}\n"
            f"• Расходы брутто: {html.escape(format_money(t['expense_gross'], cur))}\n"
            f"• Расходы нетто (без комиссии): {html.escape(format_money(t['expense_net'], cur))}\n"
            f"• Комиссия расходов: {html.escape(format_money(t['expense_fee'], cur))}\n"
            f"• Списано с депозита: {html.escape(format_money(t['expense_cash'], cur))}\n"
        )

    @staticmethod
    def _entry_line(e: Dict[str, Any]) -> str:
        cur = str(e.get("currency") or DEFAULT_CURRENCY)
        when = e.get("created_at")
        ts = ""
        if isinstance(when, datetime):
            ts = when.astimezone(MSK).strftime("%d.%m %H:%M")
        if e.get("kind") == "deposit":
            mark = "📥"
            body = f"+{format_money(e.get('amount_net'), cur)}"
        else:
            mark = "📤"
            cash = Decimal(str(e.get("amount_gross") or 0)) + Decimal(
                str(e.get("amount_fee") or 0)
            )
            cat = e.get("category_name") or "расход"
            body = f"{cat} −{format_money(cash, cur)}"
        return f"• {html.escape(ts)} {mark} {html.escape(body)}"

    def _categories_kb(self, cats: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
        rows: List[List[InlineKeyboardButton]] = []
        pair: List[InlineKeyboardButton] = []
        for c in cats:
            pair.append(
                InlineKeyboardButton(
                    text=str(c["name"])[:32],
                    callback_data=_cb("cat", str(c["id"])),
                )
            )
            if len(pair) == 2:
                rows.append(pair)
                pair = []
        if pair:
            rows.append(pair)
        rows.append(
            [InlineKeyboardButton(text="Свой вариант…", callback_data=_cb("cat", "new"))]
        )
        rows.append(
            [InlineKeyboardButton(text="❌ Отмена", callback_data=_cb("home"))]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def _cancel_kb() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data=_cb("home"))]
            ]
        )

    @staticmethod
    async def _edit_or_answer(
        callback: CallbackQuery, text: str, kb: InlineKeyboardMarkup
    ) -> None:
        if callback.message:
            try:
                await callback.message.edit_text(
                    text, parse_mode=ParseMode.HTML, reply_markup=kb
                )
                return
            except Exception:
                pass
            await callback.message.answer(
                text, parse_mode=ParseMode.HTML, reply_markup=kb
            )

    @staticmethod
    def is_ledger_state(state_name: Optional[str]) -> bool:
        if not state_name:
            return False
        return state_name.startswith("LedgerStates:")
