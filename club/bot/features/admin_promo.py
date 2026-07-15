"""Админ: FSM `/new_promo` — промо-кампании по deep link ``/start=promo_<guid>``."""

from __future__ import annotations

import logging
from typing import Any, Dict

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.admin_guard import is_telegram_admin
from bot.services.promo_campaign_service import build_promo_campaign_deeplink
from bot.texts import ru_admin_promo as pr_txt
from bot.utils.telegram_identity import resolve_telegram_bot_username
from storage.user_storage import UserStorage

logger = logging.getLogger(__name__)


class APRCallback(CallbackData, prefix="apr"):
    a: str
    v: str


class AdminPromoStates(StatesGroup):
    name = State()
    description = State()
    discount = State()
    confirm = State()


def _confirm_kb() -> InlineKeyboardMarkup:
    cb = APRCallback
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=pr_txt.BTN_CONFIRM_CREATE,
                    callback_data=cb(a="ok", v="yes").pack(),
                ),
                InlineKeyboardButton(text="❌ Нет", callback_data=cb(a="ok", v="no").pack()),
            ],
        ]
    )


def register_admin_promo_handlers(dp: Dispatcher, user_storage: UserStorage, bot: Bot) -> None:
    admin_chat_filters = (F.chat.type == ChatType.PRIVATE,)

    async def uid_ok(uid: int | None) -> bool:
        if uid is None:
            return False
        return await is_telegram_admin(user_storage, uid)

    async def _cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(pr_txt.MSG_CANCELLED)

    dp.message.register(_cancel, *admin_chat_filters, Command("cancel"))

    async def cmd_new(message: Message, state: FSMContext) -> None:
        if message.from_user is None or not await uid_ok(message.from_user.id):
            await message.reply(pr_txt.ERR_NO_ACCESS, parse_mode=ParseMode.HTML)
            return
        await state.clear()
        await state.set_state(AdminPromoStates.name)
        await message.answer(pr_txt.NEW_PROMO_PROMPT_HTML, parse_mode=ParseMode.HTML)

    dp.message.register(cmd_new, *admin_chat_filters, Command("new_promo"))

    async def recv_name(message: Message, state: FSMContext) -> None:
        if message.from_user is None or not await uid_ok(message.from_user.id):
            return
        name = (message.text or "").strip()
        if not name or len(name) > 255:
            await message.reply(pr_txt.ERR_NAME_EMPTY)
            return
        await state.update_data(name=name)
        await state.set_state(AdminPromoStates.description)
        await message.answer(pr_txt.PROMPT_DESCRIPTION_HTML, parse_mode=ParseMode.HTML)

    dp.message.register(recv_name, *admin_chat_filters, AdminPromoStates.name, F.text)

    async def recv_description(message: Message, state: FSMContext) -> None:
        if message.from_user is None or not await uid_ok(message.from_user.id):
            return
        desc = (message.text or "").strip()
        if not desc:
            await message.reply(pr_txt.ERR_DESCRIPTION_EMPTY)
            return
        await state.update_data(description=desc)
        await state.set_state(AdminPromoStates.discount)
        await message.answer(pr_txt.PROMPT_DISCOUNT_HTML, parse_mode=ParseMode.HTML)

    dp.message.register(
        recv_description, *admin_chat_filters, AdminPromoStates.description, F.text
    )

    async def recv_discount(message: Message, state: FSMContext) -> None:
        if message.from_user is None or not await uid_ok(message.from_user.id):
            return
        raw = (message.text or "").strip().replace(",", ".")
        try:
            pct = float(raw)
        except ValueError:
            await message.reply(pr_txt.ERR_DISCOUNT_FORMAT, parse_mode=ParseMode.HTML)
            return
        if pct < 1 or pct >= 100:
            await message.reply(pr_txt.ERR_DISCOUNT_FORMAT, parse_mode=ParseMode.HTML)
            return
        await state.update_data(discount_percent=pct)
        await state.set_state(AdminPromoStates.confirm)
        data = await state.get_data()
        await message.answer(
            pr_txt.confirm_promo_html(
                name=str(data.get("name") or ""),
                description=str(data.get("description") or ""),
                discount_percent=pct,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=_confirm_kb(),
        )

    dp.message.register(
        recv_discount, *admin_chat_filters, AdminPromoStates.discount, F.text
    )

    async def apr_callback(
        query: CallbackQuery, state: FSMContext, callback_data: APRCallback
    ) -> None:
        if query.message is None or query.from_user is None:
            await query.answer()
            return
        if query.message.chat.type != ChatType.PRIVATE or not await uid_ok(
            query.from_user.id
        ):
            await query.answer("⛔", show_alert=True)
            return

        a = callback_data.a
        v = callback_data.v
        st = await state.get_state()

        if a == "x" and v == "cancel":
            await state.clear()
            await query.message.edit_text(pr_txt.MSG_CALLBACK_CANCELLED)
            await query.answer()
            return

        if a == "ok" and st == AdminPromoStates.confirm.state:
            await query.answer()
            if v != "yes":
                await query.message.edit_text(pr_txt.MSG_NOT_SAVED)
                await state.clear()
                return
            data: Dict[str, Any] = await state.get_data()
            await query.message.edit_text(pr_txt.MSG_CREATING)
            guid = await user_storage.create_promo_campaign(
                name=str(data["name"]),
                description=str(data["description"]),
                discount_percent=float(data["discount_percent"]),
                created_by=query.from_user.id,
            )
            await state.clear()
            if not guid:
                await bot.send_message(
                    query.message.chat.id,
                    "❌ Не удалось сохранить (см. лог БД).",
                )
                return
            username = await resolve_telegram_bot_username(bot)
            link = (
                build_promo_campaign_deeplink(username, guid)
                if username
                else f"start=promo_{guid}"
            )
            await bot.send_message(
                query.message.chat.id,
                pr_txt.promo_created_html(
                    guid=guid,
                    deeplink=link,
                    discount_percent=data["discount_percent"],
                ),
                parse_mode=ParseMode.HTML,
            )
            logger.info(
                "Admin promo created guid=%s by=%s", guid, query.from_user.id
            )
            return

        await query.answer()

    dp.callback_query.register(apr_callback, APRCallback.filter())
