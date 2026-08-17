"""Админ-меню учёта депозита и расходов (USDT, задел под мультивалютность)."""

from __future__ import annotations

import html
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Union

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
    format_amount,
    format_money,
    parse_amount,
    parse_op_date,
    today_msk,
)
from bot.services.tg_rich import edit_rich_message, send_rich_message

logger = logging.getLogger(__name__)

_CB = "ldg"
_MONTHS_RU = (
    "",
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)


class LedgerStates(StatesGroup):
    deposit_gross = State()
    deposit_fee = State()
    deposit_note = State()
    expense_custom = State()
    expense_gross = State()
    expense_fee = State()
    expense_note = State()
    op_date = State()
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
            self._on_op_date,
            PRIVATE_CHAT,
            StateFilter(LedgerStates.op_date),
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
        cur = await state.get_state()
        if cur and cur.endswith(":op_date"):
            await message.answer(
                "Дата: сегодня, вчера или ДД.ММ / ДД.ММ.ГГГГ. Или кнопка."
            )
            return
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
            await self._show_report(callback)
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
            if not cats:
                await state.set_state(LedgerStates.expense_custom)
                await self._edit_or_answer(
                    callback,
                    "📤 <b>Расход</b> (USDT)\n\n"
                    "Статей ещё нет — напишите название:",
                    self._cancel_kb(),
                )
            else:
                await self._edit_or_answer(
                    callback,
                    "📤 <b>Расход</b> (USDT)\n\n"
                    "Выберите статью из уже записанных или введите свою:",
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
        if action == "list":
            text, kb = await self._ops_list()
            await self._edit_or_answer(callback, text, kb)
            await callback.answer()
            return
        if action == "date":
            await self._on_date_callback(callback, state, value)
            return
        if action == "del":
            await self._ask_delete(callback, value)
            return
        if action == "delok":
            await self._do_delete(callback, value)
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
        await self._ask_date(message, state)

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
        await self._ask_date(message, state)

    async def _on_op_date(self, message: Message, state: FSMContext) -> None:
        if not await self._ensure_admin(message.from_user.id if message.from_user else None):
            return
        parsed = parse_op_date(message.text or "")
        if parsed is None:
            await message.answer(
                "Не понял дату. Напишите сегодня, вчера, ДД.ММ или ДД.ММ.ГГГГ."
            )
            return
        await state.update_data(occurred_on=parsed.isoformat())
        await self._ask_confirm(message, state)

    async def _on_date_callback(
        self, callback: CallbackQuery, state: FSMContext, value: str
    ) -> None:
        cur = await state.get_state()
        if not cur or not cur.endswith(":op_date"):
            await callback.answer("Сначала заполните сумму", show_alert=True)
            return
        today = today_msk()
        if value == "today":
            parsed = today
        elif value == "yday":
            parsed = today - timedelta(days=1)
        elif value == "custom":
            await self._edit_or_answer(
                callback,
                "Напишите дату: ДД.ММ или ДД.ММ.ГГГГ",
                self._cancel_kb(),
            )
            await callback.answer()
            return
        else:
            await callback.answer("Дата?")
            return
        await state.update_data(occurred_on=parsed.isoformat())
        await self._ask_confirm(callback, state)
        await callback.answer()

    async def _ask_date(
        self, target: Union[Message, CallbackQuery], state: FSMContext
    ) -> None:
        await state.set_state(LedgerStates.op_date)
        today = today_msk()
        yday = today - timedelta(days=1)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"Сегодня ({today.strftime('%d.%m')})",
                        callback_data=_cb("date", "today"),
                    ),
                    InlineKeyboardButton(
                        text=f"Вчера ({yday.strftime('%d.%m')})",
                        callback_data=_cb("date", "yday"),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Другая дата…",
                        callback_data=_cb("date", "custom"),
                    )
                ],
                [InlineKeyboardButton(text="❌ Отмена", callback_data=_cb("home"))],
            ]
        )
        text = (
            "Дата операции (когда было пополнение или расход).\n"
            "Или напишите ДД.ММ / ДД.ММ.ГГГГ"
        )
        if isinstance(target, CallbackQuery):
            await self._edit_or_answer(target, text, kb)
        else:
            await target.answer(text, reply_markup=kb)

    async def _ask_confirm(
        self, target: Union[Message, CallbackQuery], state: FSMContext
    ) -> None:
        data = await state.get_data()
        acc = await self.user_storage.get_default_ledger_account()
        text = self._preview_html(data, acc)
        await state.set_state(LedgerStates.confirm)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Записать", callback_data=_cb("ok")),
                    InlineKeyboardButton(text="❌ Отмена", callback_data=_cb("home")),
                ]
            ]
        )
        if isinstance(target, CallbackQuery):
            await self._edit_or_answer(target, text, kb)
        else:
            await target.answer(text, parse_mode=ParseMode.HTML, reply_markup=kb)

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
        lines.append(f"• Дата: <b>{html.escape(self._fmt_occurred(data.get('occurred_on')))}</b>")
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
        occurred_on = self._parse_stored_date(data.get("occurred_on"))
        row = await self.user_storage.add_ledger_entry(
            kind=kind,
            gross=gross,
            fee=fee,
            created_by=int(uid),
            category_id=int(data["category_id"]) if data.get("category_id") else None,
            category_name=str(data.get("category_name") or "") or None,
            note=str(data.get("note") or "") or None,
            occurred_on=occurred_on,
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
                f"{html.escape(self._fmt_occurred(row.get('occurred_on')))}: "
                f"брутто {html.escape(format_money(row['amount_gross'], cur))}, "
                f"комиссия {html.escape(format_money(row['amount_fee'], cur))}\n"
                f"Остаток: <b>{html.escape(after)}</b>"
            )
        else:
            cash = Decimal(str(row["amount_gross"])) + Decimal(str(row["amount_fee"]))
            msg = (
                f"✅ Расход «{html.escape(str(row.get('category_name') or ''))}»\n"
                f"{html.escape(self._fmt_occurred(row.get('occurred_on')))}: "
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
        month_start = today_msk().replace(day=1)
        month_gross = Decimal("0")
        if acc:
            month = await self.user_storage.ledger_totals(
                account_id=int(acc["id"]), since=month_start
            )
            month_gross = month.get("expense_gross") or Decimal("0")
        lines = [
            "💸 <b>Расходы</b>",
            f"Текущий баланс: <b>{html.escape(bal)}</b>",
            f"Расходы брутто за {html.escape(_MONTHS_RU[month_start.month])}: "
            f"<b>{html.escape(format_money(month_gross, cur))}</b>",
        ]
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📥 Пополнить депозит", callback_data=_cb("dep"))],
                [InlineKeyboardButton(text="📤 Расход", callback_data=_cb("exp"))],
                [InlineKeyboardButton(text="🗑 Операции", callback_data=_cb("list"))],
                [InlineKeyboardButton(text="📊 Отчёт", callback_data=_cb("report"))],
            ]
        )
        return "\n".join(lines), kb

    async def _show_report(self, callback: CallbackQuery) -> None:
        acc = await self.user_storage.get_default_ledger_account()
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="« Меню", callback_data=_cb("home"))]
            ]
        )
        if not acc:
            await self._edit_or_answer(callback, "Счёт ещё не создан.", kb)
            return
        cur = str(acc.get("currency") or DEFAULT_CURRENCY)
        months = await self.user_storage.ledger_monthly_report(int(acc["id"]))
        rich = self._report_rich_html(acc, cur, months)
        fallback = self._report_fallback_html(acc, cur, months)
        await self._edit_or_answer_rich(callback, rich, kb, fallback=fallback)

    def _report_rich_html(
        self,
        acc: Dict[str, Any],
        cur: str,
        months: List[Dict[str, Any]],
    ) -> str:
        bal = html.escape(format_money(acc.get("balance"), cur))
        parts = [
            "<h2>📊 Отчёт</h2>",
            f"<p>Текущий баланс: <mark><b>{bal}</b></mark></p>",
        ]
        if not months:
            parts.append("<p>Пока нет операций.</p>")
            return "".join(parts)
        for m in months:
            title = self._month_title(m["month"])
            parts.append(f"<h3>{html.escape(title)}</h3>")
            parts.append(
                "<table bordered striped>"
                f"<caption>Суммы в {html.escape(cur)}</caption>"
                "<tr><th align=\"left\">Показатель</th>"
                "<th align=\"right\">Сумма</th></tr>"
                "<tr><td>🟢 Приход нетто</td>"
                f"<td align=\"right\">{self._rich_amt(m['deposit_net'], 'in')}</td></tr>"
                "<tr><td>Комса прихода</td>"
                f"<td align=\"right\">{self._rich_amt(m['deposit_fee'], 'fee')}</td></tr>"
                "<tr><td>Комса расходов</td>"
                f"<td align=\"right\">{self._rich_amt(m['expense_fee'], 'fee')}</td></tr>"
                "</table>"
            )
            cats = m.get("categories") or []
            if cats:
                cat_rows = []
                for c in cats:
                    cat_rows.append(
                        "<tr>"
                        f"<td>{html.escape(str(c.get('name') or 'без статьи'))}</td>"
                        f"<td align=\"right\">{self._rich_amt(c.get('expense_net'), 'out')}</td>"
                        "</tr>"
                    )
                parts.append(
                    "<table bordered striped>"
                    "<caption>Расходы нетто по статьям</caption>"
                    "<tr><th align=\"left\">Статья</th>"
                    "<th align=\"right\">Нетто</th></tr>"
                    + "".join(cat_rows)
                    + "<tr><td><b>Итого нетто</b></td>"
                    f"<td align=\"right\"><b>{html.escape(format_amount(m['expense_net']))}</b></td></tr>"
                    "</table>"
                )
            else:
                parts.append("<p>Расходов в этом месяце нет.</p>")
        return "".join(parts)

    @staticmethod
    def _month_title(value: Any) -> str:
        d = value
        if isinstance(d, datetime):
            d = d.date()
        if not isinstance(d, date):
            return str(value or "")
        name = _MONTHS_RU[d.month] if 1 <= d.month <= 12 else str(d.month)
        return f"{name} {d.year}".capitalize()

    @staticmethod
    def _rich_amt(value: Any, kind: str) -> str:
        s = html.escape(format_amount(value))
        if kind == "in":
            return f"<mark>{s}</mark>"
        if kind == "out":
            return f"<b>{s}</b>"
        return s

    @staticmethod
    def _report_fallback_html(
        acc: Dict[str, Any],
        cur: str,
        months: List[Dict[str, Any]],
    ) -> str:
        lines = [
            "📊 <b>Отчёт</b>",
            f"Текущий баланс: <b>{html.escape(format_money(acc.get('balance'), cur))}</b>",
            "",
        ]
        if not months:
            lines.append("Пока нет операций.")
            return "\n".join(lines)
        for m in months:
            title = LedgerFeature._month_title(m["month"])
            lines.append(f"<b>{html.escape(title)}</b>")
            lines.append(
                f"• Приход нетто: {html.escape(format_money(m['deposit_net'], cur))}"
            )
            lines.append(
                f"• Комса прихода: {html.escape(format_money(m['deposit_fee'], cur))}"
            )
            lines.append(
                f"• Комса расходов: {html.escape(format_money(m['expense_fee'], cur))}"
            )
            cats = m.get("categories") or []
            if cats:
                lines.append("Расходы нетто:")
                for c in cats:
                    lines.append(
                        f"  — {html.escape(str(c.get('name') or 'без статьи'))}: "
                        f"{html.escape(format_money(c.get('expense_net'), cur))}"
                    )
                lines.append(
                    f"  Итого нетто: {html.escape(format_money(m['expense_net'], cur))}"
                )
            else:
                lines.append("Расходов нет.")
            lines.append("")
        return "\n".join(lines)

    async def _edit_or_answer_rich(
        self,
        callback: CallbackQuery,
        html_text: str,
        kb: InlineKeyboardMarkup,
        *,
        fallback: str,
    ) -> None:
        msg = callback.message
        bot = callback.bot
        if not msg or not bot:
            return
        chat_id = msg.chat.id
        try:
            await edit_rich_message(bot, chat_id, msg.message_id, html_text, kb)
            return
        except Exception as e:
            logger.warning("edit rich report: %s", e)
        try:
            await send_rich_message(bot, chat_id, html_text, kb)
            return
        except Exception as e:
            logger.warning("send rich report: %s", e)
        await self._edit_or_answer(callback, fallback, kb)

    @staticmethod
    def _as_date(value: Any) -> Optional[date]:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str) and value.strip():
            try:
                return date.fromisoformat(value.strip()[:10])
            except ValueError:
                return None
        return None

    @classmethod
    def _parse_stored_date(cls, value: Any) -> date:
        return cls._as_date(value) or today_msk()

    @classmethod
    def _fmt_occurred(cls, value: Any) -> str:
        d = cls._as_date(value)
        return d.strftime("%d.%m.%Y") if d else "—"

    @classmethod
    def _entry_plain(cls, e: Dict[str, Any]) -> str:
        cur = str(e.get("currency") or DEFAULT_CURRENCY)
        d = cls._as_date(e.get("occurred_on"))
        ts = d.strftime("%d.%m") if d else ""
        if e.get("kind") == "deposit":
            body = f"+{format_money(e.get('amount_net'), cur)}"
        else:
            cash = Decimal(str(e.get("amount_gross") or 0)) + Decimal(
                str(e.get("amount_fee") or 0)
            )
            cat = e.get("category_name") or "расход"
            body = f"{cat} −{format_money(cash, cur)}"
        return f"{ts} {body}".strip()

    @classmethod
    def _entry_line(cls, e: Dict[str, Any]) -> str:
        return f"• {html.escape(cls._entry_plain(e))}"

    async def _ops_list(self) -> tuple[str, InlineKeyboardMarkup]:
        acc = await self.user_storage.get_default_ledger_account()
        recent = await self.user_storage.list_recent_ledger_entries(
            limit=12, account_id=int(acc["id"]) if acc else None
        )
        lines = [
            "🗑 <b>Операции</b>",
            "Нажмите запись, чтобы удалить. Остаток счёта откатится.\n",
        ]
        rows: List[List[InlineKeyboardButton]] = []
        if not recent:
            lines.append("Пока нет записей.")
        for e in recent:
            label = self._entry_plain(e)[:58]
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"🗑 {label}",
                        callback_data=_cb("del", str(int(e["id"]))),
                    )
                ]
            )
        rows.append([InlineKeyboardButton(text="« Меню", callback_data=_cb("home"))])
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    async def _ask_delete(self, callback: CallbackQuery, value: str) -> None:
        try:
            entry_id = int(value)
        except ValueError:
            await callback.answer("Запись?")
            return
        row = await self.user_storage.get_ledger_entry(entry_id)
        if not row:
            await callback.answer("Уже нет такой записи", show_alert=True)
            text, kb = await self._ops_list()
            await self._edit_or_answer(callback, text, kb)
            return
        cur = str(row.get("currency") or DEFAULT_CURRENCY)
        delta = Decimal(str(row.get("balance_delta") or 0))
        reverse = format_money(-delta, cur)
        note = str(row.get("note") or "").strip()
        text = (
            "🗑 <b>Удалить запись?</b>\n\n"
            f"{html.escape(self._entry_plain(row))}\n"
            f"Комментарий: {html.escape(note or '—')}\n"
            f"Откат остатка: <b>{html.escape(reverse)}</b>"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🗑 Удалить",
                        callback_data=_cb("delok", str(entry_id)),
                    ),
                    InlineKeyboardButton(text="Назад", callback_data=_cb("list")),
                ]
            ]
        )
        await self._edit_or_answer(callback, text, kb)
        await callback.answer()

    async def _do_delete(self, callback: CallbackQuery, value: str) -> None:
        try:
            entry_id = int(value)
        except ValueError:
            await callback.answer("Запись?")
            return
        row = await self.user_storage.delete_ledger_entry(entry_id)
        if not row:
            await callback.answer("Не удалось удалить", show_alert=True)
            text, kb = await self._ops_list()
            await self._edit_or_answer(callback, text, kb)
            return
        acc = await self.user_storage.get_default_ledger_account()
        cur = str((acc or {}).get("currency") or DEFAULT_CURRENCY)
        bal = format_money((acc or {}).get("balance") or 0, cur)
        await callback.answer("Удалено")
        text, kb = await self._ops_list()
        header = (
            f"✅ Удалено: {html.escape(self._entry_plain(row))}\n"
            f"Остаток: <b>{html.escape(bal)}</b>\n\n"
        )
        await self._edit_or_answer(callback, header + text, kb)

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
