"""Доска добрых дел: просьбы участников клуба и отклики дарителей."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.features.base import BaseFeature
from bot.filters import PRIVATE_INLINE_CALLBACK_ONLY
from bot.services import wish_board_notify as wb_notify
from bot.services.wish_board_money_guard import looks_like_money_transfer
from bot.services.wish_title_generator import generate_wish_button_title
from bot.states import WishBoardAdminStates, WishBoardStates
from bot.texts import ru_wish_board as wb_txt
from bot.texts import ru_angel_pool as ap_txt
from bot.utils.admin_channel import send_admin_html_message_main_bot
from bot.utils.inline_buttons import callback_button
from bot.utils.telegram_html import sanitize_telegram_html
from bot.utils.user_ui import render_user_screen, with_main_menu
from config import config

logger = logging.getLogger(__name__)

CB_HUB = "wb:hub"
CB_REQ = "wb:req"
CB_DON = "wb:don"
CB_MY = "wb:my"
CB_MY_DON = "wb:mydon"
CB_MY_DON_ACTIVE = "wb:mydon:active"
CB_MY_DON_DONE = "wb:mydon:done"
CB_PAYMENT = "menu_act:payment"
CB_GIFT_PAY = "payment_gift_start"
CB_TYPE_PREFIX = "wb:type:"
CB_ANON_PREFIX = "wb:anon:"
CB_SUBMIT = "wb:submit"
CB_CANCEL_CREATE = "wb:ccancel"
CB_LIST_PREFIX = "wb:list:"
CB_VIEW_PREFIX = "wb:view:"
CB_TAKE_PREFIX = "wb:take:"
CB_RELEASE_PREFIX = "wb:rel:"
CB_GIFT_PREFIX = "wb:gift:"
CB_DONE_PREFIX = "wb:done:"
CB_CONFIRM_PREFIX = "wb:cfm:"
CB_DISPUTE_PREFIX = "wb:disp:"
CB_CANCEL_WISH_PREFIX = "wb:cx:"
CB_CX_CONFIRM = "wb:cxok:"
CB_CX_KEEP = "wb:cxkeep:"
CB_CARD_EXT = "wb:card:ext"
CB_CARD_EDIT = "wb:card:edit"
CB_PEND_CANCEL_NEW = "wb:pend:cx:"
CB_PEND_WAIT = "wb:pend:wait"
CB_RATE_PREFIX = "wb:rate:"
CB_ASK_PREFIX = "wb:ask:"
CB_REPLY_PREFIX = "wb:reply:"
CB_GEN = "wb:gen"
RATING_CALLBACK_PREFIX = "wb:rate:"


class WishBoardFeature(BaseFeature):
    name = "wish_board"

    def __init__(self, user_storage, feature_manager):
        super().__init__()
        self.user_storage = user_storage
        self.feature_manager = feature_manager
        self._tg_bot: Optional[Bot] = None
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._last_digest_at: Optional[datetime] = None

    def set_bot(self, telegram_app) -> None:
        self._tg_bot = telegram_app.bot if telegram_app else None

    async def initialize(self) -> None:
        if not config.wish_board_active:
            logger.info("[%s] отключено (wish_board_active=false)", self.name)
            return
        self._scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
        self._scheduler.add_job(
            self._cron_hourly,
            CronTrigger(minute=20),
            id="wish_board_hourly",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._cron_digest,
            CronTrigger(
                hour=config.WISH_BOARD_DIGEST_HOUR,
                minute=config.WISH_BOARD_DIGEST_MINUTE,
            ),
            id="wish_board_digest",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._cron_group_reminder,
            CronTrigger(
                hour=config.WISH_BOARD_GROUP_REMINDER_HOUR,
                minute=config.WISH_BOARD_GROUP_REMINDER_MINUTE,
            ),
            id="wish_board_group_reminder",
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info(
            "[%s] scheduler started (hourly :20, digest %02d:%02d, reminder %02d:%02d МСК)",
            self.name,
            config.WISH_BOARD_DIGEST_HOUR,
            config.WISH_BOARD_DIGEST_MINUTE,
            config.WISH_BOARD_GROUP_REMINDER_HOUR,
            config.WISH_BOARD_GROUP_REMINDER_MINUTE,
        )

    async def teardown(self) -> None:
        if self._scheduler:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
            self._scheduler = None
        logger.info("[%s] остановлена", self.name)

    def register_handlers(self, dp: Dispatcher) -> None:
        if not config.wish_board_active:
            return

        dp.callback_query.register(
            self._on_callback,
            F.data.startswith("wb:"),
            PRIVATE_INLINE_CALLBACK_ONLY,
        )
        dp.callback_query.register(
            self._on_admin_callback,
            F.data.startswith(wb_notify.CB_ADM_APPROVE)
            | F.data.startswith(wb_notify.CB_ADM_REJECT),
        )

        gid = config.resolved_admin_group_id()
        if gid:
            dp.message.register(
                self._admin_reject_reason,
                F.chat.id == gid,
                WishBoardAdminStates.waiting_reject_reason,
                F.text,
            )

    def hub_keyboard(self) -> InlineKeyboardMarkup:
        rows: List[List[InlineKeyboardButton]] = [
            [callback_button(wb_txt.BTN_REQUESTER, CB_REQ)],
            [callback_button(wb_txt.BTN_DONOR, CB_DON, style="success")],
            [callback_button(ap_txt.BTN_ANGEL, "ap:intro", style="success")],
            [callback_button(wb_txt.BTN_MY_WISHES, CB_MY)],
            [callback_button(wb_txt.BTN_MY_DONATIONS, CB_MY_DON)],
        ]
        if self.feature_manager.get("payment"):
            rows.append(
                [
                    InlineKeyboardButton(
                        text=wb_txt.BTN_GIFT_SPECIFIC_PERSON,
                        callback_data=CB_GIFT_PAY,
                    )
                ]
            )
        return with_main_menu(rows)

    def _not_member_keyboard(self) -> InlineKeyboardMarkup:
        return with_main_menu(
            [
                [
                    InlineKeyboardButton(
                        text=wb_txt.BTN_GO_PAYMENT,
                        callback_data=CB_PAYMENT,
                    )
                ],
                [InlineKeyboardButton(text=wb_txt.BTN_BACK_HUB, callback_data=CB_HUB)],
            ],
            include=True,
        )

    async def _show_not_member(self, message: Message, *, edit: bool) -> None:
        await render_user_screen(
            message,
            text=wb_txt.NOT_MEMBER_HTML,
            reply_markup=self._not_member_keyboard(),
            edit=edit,
            add_main_menu=False,
        )

    def _description_prompt(self, gift_type: str) -> str:
        if gift_type == "subscription":
            return wb_txt.MODERATION_PROMPT_SUBSCRIPTION_HTML
        return wb_txt.MODERATION_PROMPT_OTHER_HTML

    def _choose_type_keyboard(self) -> InlineKeyboardMarkup:
        return with_main_menu(
            [
                [
                    InlineKeyboardButton(
                        text=wb_txt.BTN_TYPE_SUBSCRIPTION,
                        callback_data=CB_TYPE_PREFIX + "subscription",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=wb_txt.BTN_TYPE_OTHER,
                        callback_data=CB_TYPE_PREFIX + "other",
                    )
                ],
                [InlineKeyboardButton(text=wb_txt.BTN_BACK_HUB, callback_data=CB_HUB)],
            ]
        )

    async def _show_choose_type(self, message: Message, *, edit: bool = True) -> None:
        await render_user_screen(
            message,
            text=wb_txt.CHOOSE_TYPE_HTML,
            reply_markup=self._choose_type_keyboard(),
            edit=edit,
            add_main_menu=False,
        )

    async def _edit_digest(self, wish: Optional[Dict[str, Any]]) -> None:
        if not self._tg_bot or not wish:
            return
        try:
            await wb_notify.edit_digest_wish_post(
                self._tg_bot, self.user_storage, wish
            )
        except Exception as e:
            logger.warning("wish_board digest edit wish=%s: %s", wish.get("id"), e)

    async def show_hub(
        self, message: Message, *, edit: bool = False
    ) -> None:
        text = wb_txt.HUB_TITLE_HTML
        if self.feature_manager.get("payment"):
            text = f"{text}\n\n{wb_txt.HUB_GIFT_HINT_HTML}"
        await render_user_screen(
            message,
            text=text,
            reply_markup=self.hub_keyboard(),
            edit=edit,
            add_main_menu=False,
        )

    async def try_open_from_start(
        self, message: Message, state: FSMContext, param: str
    ) -> bool:
        """Deep link из клубного дайджеста: ``/start=ddd*`` → экран ДДД в личке."""
        from bot.services.wish_board_deeplink import parse_wish_board_start_param

        if not config.wish_board_active:
            return False
        target = parse_wish_board_start_param(param)
        if not target:
            return False

        await state.clear()
        uid = message.from_user.id
        if target.kind == "hub":
            await self.show_hub(message, edit=False)
        elif target.kind == "donor":
            await self._show_pool(message, uid, page=0, edit=False)
        elif target.kind == "angel":
            ap = self.feature_manager.get("angel_pool")
            if ap:
                await ap.show_intro(message, state, edit=False)
            else:
                await self.show_hub(message, edit=False)
        elif target.kind == "wish" and target.wish_id:
            await self._show_wish_detail(
                message, uid, target.wish_id, edit=False
            )
        else:
            return False
        return True

    async def handle_description(
        self, message: Message, state: FSMContext, text: str
    ) -> None:
        desc = (text or "").strip()
        if len(desc) < 10:
            await message.answer(wb_txt.ERR_DESC_TOO_SHORT)
            return
        if len(desc) > 1500:
            await message.answer(wb_txt.ERR_DESC_TOO_LONG)
            return

        data = await state.get_data()
        gift_type = data.get("wb_gift_type")
        is_anonymous = bool(data.get("wb_anonymous", False))
        if gift_type not in ("subscription", "other"):
            await state.clear()
            await message.answer(wb_txt.ERR_SESSION_EXPIRED)
            return

        user_id = message.from_user.id
        if not await self.user_storage.user_has_active_license(user_id):
            await state.clear()
            await self._show_not_member(message, edit=False)
            return

        if looks_like_money_transfer(desc):
            logger.info(
                "wish_board card_blocked uid=%s gift_type=%s",
                user_id,
                gift_type,
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=wb_txt.BTN_ASK_EXTENSION,
                            callback_data=CB_CARD_EXT,
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=wb_txt.BTN_EDIT_DESC,
                            callback_data=CB_CARD_EDIT,
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=wb_txt.BTN_BACK_HUB, callback_data=CB_HUB
                        )
                    ],
                ]
            )
            await render_user_screen(
                message,
                text=wb_txt.CARD_BLOCKED_HTML,
                reply_markup=kb,
                edit=False,
                add_main_menu=False,
            )
            return

        pending = await self.user_storage.wish_get_pending_moderation_for_requester(
            user_id
        )
        if pending:
            wid = int(pending["id"])
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=wb_txt.BTN_CANCEL_AND_NEW,
                            callback_data=f"{CB_PEND_CANCEL_NEW}{wid}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=wb_txt.BTN_WAIT_MODERATION,
                            callback_data=CB_PEND_WAIT,
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=wb_txt.BTN_BACK_HUB, callback_data=CB_HUB
                        )
                    ],
                ]
            )
            await render_user_screen(
                message,
                text=wb_txt.pending_exists_html(pending),
                reply_markup=kb,
                edit=False,
                add_main_menu=False,
            )
            return

        active = await self.user_storage.wish_count_active_for_requester(user_id)
        if active >= config.WISH_BOARD_MAX_ACTIVE_PER_REQUESTER:
            await state.clear()
            await render_user_screen(
                message, text=wb_txt.LIMIT_REACHED_HTML, edit=False
            )
            return

        expires_at = datetime.now(timezone.utc) + timedelta(
            days=config.WISH_BOARD_DEFAULT_EXPIRE_DAYS
        )
        wish_id = await self.user_storage.wish_create(
            requester_user_id=user_id,
            gift_type=gift_type,
            description=desc,
            is_anonymous=is_anonymous,
            expires_at=expires_at,
        )
        if not wish_id:
            await state.clear()
            await message.answer(wb_txt.ERR_CREATE_FAILED)
            return

        button_title = await generate_wish_button_title(
            description=desc,
            gift_type=gift_type,
        )
        await self.user_storage.wish_set_button_title(wish_id, button_title)

        await state.clear()

        wish = await self.user_storage.wish_get(wish_id)
        requester = await self.user_storage.get_user(user_id)
        if self._tg_bot and wish:
            msg_id = await wb_notify.post_moderation_request(
                self._tg_bot,
                wish=wish,
                requester=requester,
                user_storage=self.user_storage,
            )
            if msg_id:
                await self.user_storage.wish_set_admin_notice_message_id(
                    wish_id, msg_id
                )

        await render_user_screen(
            message, text=wb_txt.CREATED_PENDING_HTML, edit=False
        )

    async def _on_callback(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        await callback.answer()
        if not callback.message or not callback.data:
            return
        data = callback.data
        msg = callback.message
        uid = callback.from_user.id

        if data == CB_HUB:
            await state.clear()
            await self.show_hub(msg, edit=True)
            return

        if data == CB_REQ:
            if not await self.user_storage.user_has_active_license(uid):
                await self._show_not_member(msg, edit=True)
                return
            await self._show_choose_type(msg, edit=True)
            return

        if data == CB_CARD_EXT:
            await state.update_data(wb_gift_type="subscription")
            await state.set_state(WishBoardStates.waiting_description)
            await render_user_screen(
                msg,
                text=wb_txt.MODERATION_PROMPT_SUBSCRIPTION_HTML,
                edit=True,
            )
            return

        if data == CB_CARD_EDIT:
            fsm = await state.get_data()
            gift_type = fsm.get("wb_gift_type") or "other"
            await state.set_state(WishBoardStates.waiting_description)
            await render_user_screen(
                msg,
                text=self._description_prompt(gift_type),
                edit=True,
            )
            return

        if data.startswith(CB_PEND_CANCEL_NEW):
            try:
                wish_id = int(data.replace(CB_PEND_CANCEL_NEW, ""))
            except ValueError:
                return
            cancelled = await self.user_storage.wish_cancel(wish_id, uid)
            if cancelled and self._tg_bot:
                await wb_notify.post_admin_lifecycle(
                    self._tg_bot,
                    event=wb_txt.ADM_EVENT_CANCELLED,
                    wish=cancelled,
                )
                await self._edit_digest(cancelled)
            await self._show_choose_type(msg, edit=True)
            return

        if data == CB_PEND_WAIT:
            await state.clear()
            await self.show_hub(msg, edit=True)
            return

        if data.startswith(CB_TYPE_PREFIX):
            gift_type = data.replace(CB_TYPE_PREFIX, "")
            if gift_type not in ("subscription", "other"):
                return
            await state.update_data(wb_gift_type=gift_type)
            kb = with_main_menu(
                [
                    [InlineKeyboardButton(text=wb_txt.BTN_ANON_NO, callback_data=CB_ANON_PREFIX + "0")],
                    [InlineKeyboardButton(text=wb_txt.BTN_ANON_YES, callback_data=CB_ANON_PREFIX + "1")],
                    [InlineKeyboardButton(text=wb_txt.BTN_BACK_HUB, callback_data=CB_HUB)],
                ]
            )
            await render_user_screen(
                msg,
                text=wb_txt.CHOOSE_ANON_HTML,
                reply_markup=kb,
                edit=True,
                add_main_menu=False,
            )
            return

        if data.startswith(CB_ANON_PREFIX):
            is_anon = data.endswith("1")
            fsm = await state.get_data()
            gift_type = fsm.get("wb_gift_type") or ""
            await state.update_data(wb_anonymous=is_anon)
            await state.set_state(WishBoardStates.waiting_description)
            await render_user_screen(
                msg,
                text=self._description_prompt(gift_type),
                edit=True,
            )
            return

        if data == CB_DON:
            await self._show_pool(msg, uid, page=0)
            return

        if data.startswith(CB_LIST_PREFIX):
            try:
                page = int(data.replace(CB_LIST_PREFIX, ""))
            except ValueError:
                page = 0
            await self._show_pool(msg, uid, page=page)
            return

        if data == CB_MY:
            await self._show_my_wishes(msg, uid)
            return

        if data == CB_MY_DON:
            await self._show_my_donations_menu(msg)
            return

        if data == CB_GEN:
            await self._show_generosity_board(msg)
            return

        if data == CB_MY_DON_ACTIVE:
            await self._show_my_donations_list(msg, uid, scope="active")
            return

        if data == CB_MY_DON_DONE:
            await self._show_my_donations_list(msg, uid, scope="done")
            return

        if data.startswith(CB_VIEW_PREFIX):
            try:
                wish_id = int(data.replace(CB_VIEW_PREFIX, ""))
            except ValueError:
                return
            await self._show_wish_detail(msg, uid, wish_id)
            return

        if data.startswith(CB_TAKE_PREFIX):
            wish_id = int(data.replace(CB_TAKE_PREFIX, ""))
            await self._take_wish(msg, uid, wish_id)
            return

        if data.startswith(CB_RELEASE_PREFIX):
            wish_id = int(data.replace(CB_RELEASE_PREFIX, ""))
            await self._release_wish(msg, uid, wish_id)
            return

        if data.startswith(CB_GIFT_PREFIX):
            wish_id = int(data.replace(CB_GIFT_PREFIX, ""))
            await self._start_gift_for_wish(msg, state, uid, wish_id)
            return

        if data.startswith(CB_DONE_PREFIX):
            wish_id = int(data.replace(CB_DONE_PREFIX, ""))
            await self._mark_done(msg, uid, wish_id)
            return

        if data.startswith(CB_CONFIRM_PREFIX):
            wish_id = int(data.replace(CB_CONFIRM_PREFIX, ""))
            await self._confirm_done(msg, uid, wish_id)
            return

        if data.startswith(CB_DISPUTE_PREFIX):
            wish_id = int(data.replace(CB_DISPUTE_PREFIX, ""))
            await self._dispute(msg, uid, wish_id)
            return

        if data.startswith(CB_CX_CONFIRM):
            try:
                wish_id = int(data.replace(CB_CX_CONFIRM, ""))
            except ValueError:
                return
            await self._cancel_wish(msg, uid, wish_id)
            return

        if data.startswith(CB_CX_KEEP):
            try:
                wish_id = int(data.replace(CB_CX_KEEP, ""))
            except ValueError:
                return
            await self._show_wish_detail(msg, uid, wish_id)
            return

        if data.startswith(CB_CANCEL_WISH_PREFIX):
            try:
                wish_id = int(data.replace(CB_CANCEL_WISH_PREFIX, ""))
            except ValueError:
                return
            wish = await self.user_storage.wish_get(wish_id)
            if (
                wish
                and wish.get("status") == "taken"
                and int(wish.get("requester_user_id") or 0) == uid
            ):
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=wb_txt.BTN_CONFIRM_CANCEL,
                                callback_data=f"{CB_CX_CONFIRM}{wish_id}",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text=wb_txt.BTN_KEEP_WISH,
                                callback_data=f"{CB_CX_KEEP}{wish_id}",
                            )
                        ],
                    ]
                )
                await render_user_screen(
                    msg,
                    text=wb_txt.CONFIRM_CANCEL_TAKEN_HTML,
                    reply_markup=kb,
                    edit=True,
                    add_main_menu=False,
                )
                return
            await self._cancel_wish(msg, uid, wish_id)
            return

        if data.startswith(CB_RATE_PREFIX):
            rest = data.replace(CB_RATE_PREFIX, "")
            parts = rest.split(":")
            if len(parts) != 2:
                return
            wish_id, rating = int(parts[0]), int(parts[1])
            await self._rate_donor(msg, uid, wish_id, rating)
            return

        if data.startswith(CB_ASK_PREFIX):
            wish_id = int(data.replace(CB_ASK_PREFIX, ""))
            await self._start_clarification(msg, state, uid, wish_id)
            return

        if data.startswith(CB_REPLY_PREFIX):
            wish_id = int(data.replace(CB_REPLY_PREFIX, ""))
            await self._start_clarification_reply(msg, state, uid, wish_id)
            return

    async def handle_clarification(
        self, message: Message, state: FSMContext, text: str
    ) -> None:
        data = await state.get_data()
        wish_id = int(data.get("wb_clarify_wish_id") or 0)
        role = data.get("wb_clarify_role")
        body = (text or "").strip()
        if len(body) < 3:
            await message.answer(wb_txt.ERR_CLARIFY_TOO_SHORT)
            return
        if not wish_id or not self._tg_bot:
            await state.clear()
            await message.answer(wb_txt.ERR_CLARIFY_NOT_ALLOWED)
            return

        wish = await self.user_storage.wish_get(wish_id)
        if not wish or wish.get("status") != "taken":
            await state.clear()
            await message.answer(wb_txt.ERR_CLARIFY_NOT_ALLOWED)
            return

        if role == "donor":
            if int(wish.get("donor_user_id") or 0) != message.from_user.id:
                await state.clear()
                await message.answer(wb_txt.ERR_CLARIFY_NOT_ALLOWED)
                return
            req_id = int(wish["requester_user_id"])
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=wb_txt.BTN_REPLY_CLARIFY,
                            callback_data=f"{CB_REPLY_PREFIX}{wish_id}",
                        )
                    ]
                ]
            )
            await wb_notify.notify_user_html(
                self._tg_bot,
                req_id,
                wb_txt.clarify_to_requester_html(wish_id, body),
                reply_markup=kb,
            )
            await state.clear()
            await message.answer(wb_txt.CLARIFY_SENT_DONOR_HTML)
            return

        if role == "requester":
            if int(wish["requester_user_id"]) != message.from_user.id:
                await state.clear()
                await message.answer(wb_txt.ERR_CLARIFY_NOT_ALLOWED)
                return
            donor_id = wish.get("donor_user_id")
            if not donor_id:
                await state.clear()
                await message.answer(wb_txt.ERR_CLARIFY_NOT_ALLOWED)
                return
            await wb_notify.notify_user_html(
                self._tg_bot,
                int(donor_id),
                wb_txt.clarify_to_donor_html(wish_id, body),
            )
            await state.clear()
            await message.answer(wb_txt.CLARIFY_SENT_REQUESTER_HTML)
            return

        await state.clear()
        await message.answer(wb_txt.ERR_CLARIFY_NOT_ALLOWED)

    async def _start_clarification(
        self, message: Message, state: FSMContext, user_id: int, wish_id: int
    ) -> None:
        wish = await self.user_storage.wish_get(wish_id)
        if not wish or wish.get("status") != "taken":
            await render_user_screen(
                message, text=wb_txt.ERR_CLARIFY_NOT_ALLOWED, edit=True
            )
            return
        if int(wish.get("donor_user_id") or 0) != user_id:
            await render_user_screen(
                message, text=wb_txt.ERR_CLARIFY_NOT_ALLOWED, edit=True
            )
            return

        if not wish.get("is_anonymous"):
            requester = await self.user_storage.get_user(
                int(wish["requester_user_id"])
            ) or {}
            contact = wb_txt.clarify_dm_contact_html(requester)
            await render_user_screen(
                message,
                text=wb_txt.CLARIFY_DM_HTML.format(contact=contact),
                edit=True,
            )
            return

        await state.set_state(WishBoardStates.waiting_clarification)
        await state.update_data(wb_clarify_wish_id=wish_id, wb_clarify_role="donor")
        await render_user_screen(
            message,
            text=wb_txt.CLARIFY_ANON_PROMPT_HTML,
            edit=True,
        )

    async def _start_clarification_reply(
        self, message: Message, state: FSMContext, user_id: int, wish_id: int
    ) -> None:
        wish = await self.user_storage.wish_get(wish_id)
        if not wish or wish.get("status") != "taken":
            await render_user_screen(
                message, text=wb_txt.ERR_CLARIFY_NOT_ALLOWED, edit=True
            )
            return
        if int(wish["requester_user_id"]) != user_id:
            await render_user_screen(
                message, text=wb_txt.ERR_CLARIFY_NOT_ALLOWED, edit=True
            )
            return
        await state.set_state(WishBoardStates.waiting_clarification_reply)
        await state.update_data(
            wb_clarify_wish_id=wish_id, wb_clarify_role="requester"
        )
        await render_user_screen(
            message,
            text=wb_txt.CLARIFY_REPLY_PROMPT_HTML,
            edit=True,
        )

    async def _show_pool(
        self, message: Message, user_id: int, *, page: int, edit: bool = True
    ) -> None:
        per_page = 5
        wishes = await self.user_storage.wish_list_open(
            limit=per_page + 1, offset=page * per_page
        )
        if not wishes:
            await render_user_screen(
                message, text=wb_txt.POOL_EMPTY_HTML, edit=edit
            )
            return

        has_more = len(wishes) > per_page
        wishes = wishes[:per_page]
        rows: List[List[InlineKeyboardButton]] = []
        for w in wishes:
            wid = int(w["id"])
            label = wb_txt.wish_button_label(w)
            rows.append(
                [
                    callback_button(
                        label,
                        f"{CB_VIEW_PREFIX}{wid}",
                    )
                ]
            )
        nav: List[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(text="◀️", callback_data=f"{CB_LIST_PREFIX}{page - 1}")
            )
        if has_more:
            nav.append(
                InlineKeyboardButton(text="▶️", callback_data=f"{CB_LIST_PREFIX}{page + 1}")
            )
        if nav:
            rows.append(nav)
        rows.append(
            [InlineKeyboardButton(text=wb_txt.BTN_BACK_HUB, callback_data=CB_HUB)]
        )

        await render_user_screen(
            message,
            text=wb_txt.POOL_TITLE_HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            edit=edit,
            add_main_menu=False,
        )

    async def _show_my_wishes(self, message: Message, user_id: int) -> None:
        wishes = await self.user_storage.wish_list_by_requester(user_id, limit=8)
        if not wishes:
            kb = with_main_menu(
                [[InlineKeyboardButton(text=wb_txt.BTN_BACK_HUB, callback_data=CB_HUB)]]
            )
            await render_user_screen(
                message,
                text=wb_txt.MY_WISHES_EMPTY_HTML,
                reply_markup=kb,
                edit=True,
                add_main_menu=False,
            )
            return
        rows = []
        for w in wishes:
            wid = int(w["id"])
            st = wb_txt.status_label_for_viewer(w, user_id)
            label = f"{wb_txt.wish_button_label(w)} · {st}"
            rows.append(
                [
                    callback_button(
                        label,
                        f"{CB_VIEW_PREFIX}{wid}",
                    )
                ]
            )
        rows.append(
            [InlineKeyboardButton(text=wb_txt.BTN_BACK_HUB, callback_data=CB_HUB)]
        )
        await render_user_screen(
            message,
            text=wb_txt.MY_WISHES_TITLE_HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            edit=True,
            add_main_menu=False,
        )

    async def _show_my_donations_menu(self, message: Message) -> None:
        kb = with_main_menu(
            [
                [
                    InlineKeyboardButton(
                        text=wb_txt.BTN_MY_DONATIONS_ACTIVE,
                        callback_data=CB_MY_DON_ACTIVE,
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=wb_txt.BTN_MY_DONATIONS_DONE,
                        callback_data=CB_MY_DON_DONE,
                    )
                ],
                [InlineKeyboardButton(text=wb_txt.BTN_BACK_HUB, callback_data=CB_HUB)],
            ]
        )
        await render_user_screen(
            message,
            text=wb_txt.MY_DONATIONS_TITLE_HTML,
            reply_markup=kb,
            edit=True,
            add_main_menu=False,
        )

    async def _show_my_donations_list(
        self, message: Message, user_id: int, *, scope: str
    ) -> None:
        wishes = await self.user_storage.wish_list_by_donor(
            user_id, scope=scope, limit=15
        )
        if scope == "done":
            empty_text = wb_txt.MY_DONATIONS_EMPTY_DONE_HTML
            title = wb_txt.MY_DONATIONS_DONE_TITLE_HTML
        else:
            empty_text = wb_txt.MY_DONATIONS_EMPTY_ACTIVE_HTML
            title = wb_txt.MY_DONATIONS_ACTIVE_TITLE_HTML

        if not wishes:
            kb = with_main_menu(
                [
                    [InlineKeyboardButton(text=wb_txt.BTN_BACK_HUB, callback_data=CB_HUB)],
                ]
            )
            await render_user_screen(
                message,
                text=empty_text,
                reply_markup=kb,
                edit=True,
                add_main_menu=False,
            )
            return

        rows = []
        for w in wishes:
            wid = int(w["id"])
            st = wb_txt.status_label_for_viewer(w, user_id)
            label = f"{wb_txt.wish_button_label(w)} · {st}"
            rows.append(
                [
                    callback_button(
                        label,
                        f"{CB_VIEW_PREFIX}{wid}",
                    )
                ]
            )
        rows.append(
            [InlineKeyboardButton(text=wb_txt.BTN_BACK_HUB, callback_data=CB_HUB)]
        )
        await render_user_screen(
            message,
            text=title,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            edit=True,
            add_main_menu=False,
        )

    async def _show_wish_detail(
        self,
        message: Message,
        user_id: int,
        wish_id: int,
        *,
        edit: bool = True,
    ) -> None:
        wish = await self.user_storage.wish_get(wish_id)
        if not wish:
            await render_user_screen(
                message, text=wb_txt.ERR_WISH_NOT_FOUND, edit=edit
            )
            return

        requester = None
        if not wish.get("is_anonymous") or int(wish["requester_user_id"]) == user_id:
            requester = await self.user_storage.get_user(
                int(wish["requester_user_id"])
            )

        text = wb_txt.format_wish_card(
            wish,
            requester=requester,
            viewer_user_id=user_id,
        )
        text = wb_txt.wish_card_with_passive_banner(
            wish, text, viewer_user_id=user_id
        )
        text = self._append_role_hint(wish, user_id, text)
        kb_rows = self._detail_actions(wish, user_id)
        await render_user_screen(
            message,
            text=text,
            reply_markup=with_main_menu(kb_rows) if kb_rows else None,
            edit=edit,
            add_main_menu=not kb_rows,
        )

    async def _show_generosity_board(self, message: Message) -> None:
        rows = await self.user_storage.generosity_leaderboard(limit=15)
        kb = with_main_menu(
            [[InlineKeyboardButton(text=wb_txt.BTN_BACK_HUB, callback_data=CB_HUB)]]
        )
        await render_user_screen(
            message,
            text=wb_txt.generosity_leaderboard_html(rows),
            reply_markup=kb,
            edit=True,
            add_main_menu=False,
        )

    def _append_role_hint(
        self, wish: Dict[str, Any], user_id: int, text: str
    ) -> str:
        status = wish.get("status")
        donor_id = wish.get("donor_user_id")
        if status != "taken" or not donor_id or int(donor_id) != user_id:
            return text
        if wish.get("gift_type") == "subscription":
            return f"{text}\n\n{wb_txt.TAKEN_OK_SUBSCRIPTION_HTML}"
        return f"{text}\n\n{wb_txt.TAKEN_OK_OTHER_HTML}"

    def _detail_actions(
        self, wish: Dict[str, Any], user_id: int
    ) -> List[List[InlineKeyboardButton]]:
        wid = int(wish["id"])
        status = wish.get("status")
        requester_id = int(wish["requester_user_id"])
        donor_id = wish.get("donor_user_id")
        rows: List[List[InlineKeyboardButton]] = []

        if wb_txt.is_passive_wish_viewer(wish, user_id):
            return [
                [
                    InlineKeyboardButton(
                        text=wb_txt.BTN_OTHER_WISHES, callback_data=CB_DON
                    )
                ],
                [InlineKeyboardButton(text=wb_txt.BTN_BACK_HUB, callback_data=CB_HUB)],
            ]

        if status == "open" and user_id != requester_id:
            rows.append(
                [
                    callback_button(
                        wb_txt.BTN_TAKE,
                        f"{CB_TAKE_PREFIX}{wid}",
                        style="success",
                    )
                ]
            )
        if status == "taken" and donor_id and int(donor_id) == user_id:
            if wish.get("gift_type") == "subscription":
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=wb_txt.BTN_GIFT_SUB,
                            callback_data=f"{CB_GIFT_PREFIX}{wid}",
                        )
                    ]
                )
            else:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=wb_txt.BTN_MARK_DONE,
                            callback_data=f"{CB_DONE_PREFIX}{wid}",
                        )
                    ]
                )
            rows.append(
                [
                    InlineKeyboardButton(
                        text=wb_txt.BTN_ASK_CLARIFY,
                        callback_data=f"{CB_ASK_PREFIX}{wid}",
                    )
                ]
            )
            rows.append(
                [InlineKeyboardButton(text=wb_txt.BTN_RELEASE, callback_data=f"{CB_RELEASE_PREFIX}{wid}")]
            )
        if (
            status == "done_pending"
            and user_id == requester_id
            and wish.get("gift_type") != "subscription"
        ):
            rows.append(
                [InlineKeyboardButton(text=wb_txt.BTN_CONFIRM, callback_data=f"{CB_CONFIRM_PREFIX}{wid}")]
            )
            rows.append(
                [InlineKeyboardButton(text=wb_txt.BTN_DISPUTE, callback_data=f"{CB_DISPUTE_PREFIX}{wid}")]
            )
        if status in ("pending_moderation", "open", "taken") and user_id == requester_id:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=wb_txt.BTN_CANCEL_WISH,
                        callback_data=f"{CB_CANCEL_WISH_PREFIX}{wid}",
                    )
                ]
            )
        rows.append(
            [InlineKeyboardButton(text=wb_txt.BTN_BACK_HUB, callback_data=CB_HUB)]
        )
        return rows

    async def _take_wish(
        self, message: Message, user_id: int, wish_id: int
    ) -> None:
        if await self.user_storage.wish_count_taken_by_donor(user_id) >= 1:
            await render_user_screen(
                message,
                text=wb_txt.ERR_DONOR_BUSY,
                edit=True,
            )
            return
        wish = await self.user_storage.wish_take(wish_id, user_id)
        if not wish:
            await render_user_screen(
                message, text=wb_txt.ERR_TAKE_FAILED, edit=True
            )
            return
        if self._tg_bot:
            await wb_notify.post_admin_lifecycle(
                self._tg_bot, event=wb_txt.ADM_EVENT_TAKEN, wish=wish
            )
            req_id = int(wish["requester_user_id"])
            await wb_notify.notify_user_html(
                self._tg_bot,
                req_id,
                wb_txt.notify_taken_requester_html(wish),
            )
            await self._edit_digest(wish)
        await self._show_wish_detail(message, user_id, wish_id)

    async def _release_wish(
        self, message: Message, user_id: int, wish_id: int
    ) -> None:
        wish = await self.user_storage.wish_release(wish_id, user_id)
        if not wish:
            await render_user_screen(message, text=wb_txt.ERR_RELEASE_FAILED, edit=True)
            return
        if self._tg_bot:
            await wb_notify.post_admin_lifecycle(
                self._tg_bot, event=wb_txt.ADM_EVENT_RELEASED, wish=wish
            )
            await self._edit_digest(wish)
        await self._show_wish_detail(message, user_id, wish_id)

    async def _start_gift_for_wish(
        self, message: Message, state: FSMContext, user_id: int, wish_id: int
    ) -> None:
        wish = await self.user_storage.wish_get(wish_id)
        if not wish or wish.get("status") != "taken":
            return
        if int(wish.get("donor_user_id") or 0) != user_id:
            return
        recipient_id = int(wish["requester_user_id"])
        mgift = self.feature_manager.get("member_gift_extension")
        if not mgift:
            await render_user_screen(
                message, text=wb_txt.ERR_GIFT_UNAVAILABLE, edit=True
            )
            return
        await mgift.start_for_recipient(
            message,
            state,
            recipient_id,
            edit=True,
            wish_id=wish_id,
            hide_recipient_identity=bool(wish.get("is_anonymous")),
        )

    async def _mark_done(
        self, message: Message, user_id: int, wish_id: int
    ) -> None:
        wish = await self.user_storage.wish_get(wish_id)
        if wish and wish.get("gift_type") == "subscription":
            await render_user_screen(
                message,
                text=wb_txt.TAKEN_OK_SUBSCRIPTION_HTML,
                edit=True,
            )
            return
        wish = await self.user_storage.wish_mark_done(wish_id, user_id)
        if not wish:
            await render_user_screen(message, text=wb_txt.ERR_MARK_DONE_FAILED, edit=True)
            return
        if self._tg_bot:
            await wb_notify.post_admin_lifecycle(
                self._tg_bot, event=wb_txt.ADM_EVENT_DONE_PENDING, wish=wish
            )
            req_id = int(wish["requester_user_id"])
            kb = with_main_menu(
                [
                    [
                        InlineKeyboardButton(
                            text=wb_txt.BTN_CONFIRM,
                            callback_data=f"{CB_CONFIRM_PREFIX}{wish_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=wb_txt.BTN_DISPUTE,
                            callback_data=f"{CB_DISPUTE_PREFIX}{wish_id}",
                        )
                    ],
                ]
            )
            await wb_notify.notify_user_html(
                self._tg_bot,
                req_id,
                wb_txt.DONE_PENDING_REQUESTER_HTML,
                reply_markup=kb,
            )
            await self._edit_digest(wish)
        await render_user_screen(
            message, text=wb_txt.MARK_DONE_OK_HTML, edit=True
        )

    async def _confirm_done(
        self, message: Message, user_id: int, wish_id: int
    ) -> None:
        wish = await self.user_storage.wish_confirm(wish_id, user_id)
        if not wish:
            await render_user_screen(message, text=wb_txt.ERR_CONFIRM_FAILED, edit=True)
            return
        if self._tg_bot:
            await wb_notify.post_admin_lifecycle(
                self._tg_bot, event=wb_txt.ADM_EVENT_COMPLETED, wish=wish
            )
            donor_id = wish.get("donor_user_id")
            if donor_id:
                await wb_notify.notify_user_html(
                    self._tg_bot,
                    int(donor_id),
                    wb_txt.NOTIFY_DONOR_CONFIRMED_HTML,
                )
            await wb_notify.reply_group_wish_fulfilled(self._tg_bot, wish)
            await self._edit_digest(wish)
        kb = wb_notify.rating_prompt_markup(wish_id)
        await render_user_screen(
            message,
            text=wb_txt.COMPLETED_HTML,
            reply_markup=kb,
            edit=True,
            add_main_menu=False,
        )

    async def _dispute(
        self, message: Message, user_id: int, wish_id: int
    ) -> None:
        wish = await self.user_storage.wish_dispute(wish_id, user_id)
        if not wish:
            await render_user_screen(
                message, text=wb_txt.ERR_DISPUTE_FAILED, edit=True
            )
            return
        if self._tg_bot:
            await wb_notify.post_admin_lifecycle(
                self._tg_bot,
                event=wb_txt.ADM_EVENT_DISPUTE,
                wish=wish,
            )
            donor_id = wish.get("donor_user_id")
            if donor_id:
                await wb_notify.notify_user_html(
                    self._tg_bot,
                    int(donor_id),
                    wb_txt.notify_dispute_donor_html(wish),
                )
            await self._edit_digest(wish)
        await render_user_screen(
            message,
            text=wb_txt.DISPUTE_OK_HTML,
            edit=True,
        )

    async def _cancel_wish(
        self, message: Message, user_id: int, wish_id: int
    ) -> None:
        wish = await self.user_storage.wish_get(wish_id)
        was_taken = bool(wish and wish.get("status") == "taken")
        donor_id = int(wish.get("donor_user_id") or 0) if wish else 0
        is_anonymous = bool(wish.get("is_anonymous")) if wish else True

        cancelled = await self.user_storage.wish_cancel(wish_id, user_id)
        if not cancelled:
            await render_user_screen(message, text=wb_txt.ERR_CANCEL_FAILED, edit=True)
            return
        if self._tg_bot:
            await wb_notify.post_admin_lifecycle(
                self._tg_bot, event=wb_txt.ADM_EVENT_CANCELLED, wish=cancelled
            )
            if was_taken and donor_id:
                kb = None
                if not is_anonymous:
                    req_id = int(cancelled["requester_user_id"])
                    kb = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text=wb_txt.BTN_WRITE_REQUESTER,
                                    url=f"tg://user?id={req_id}",
                                )
                            ]
                        ]
                    )
                await wb_notify.notify_user_html(
                    self._tg_bot,
                    donor_id,
                    wb_txt.donor_cancelled_by_requester_html(cancelled),
                    reply_markup=kb,
                )
            await self._edit_digest(cancelled)
        await self._show_my_wishes(message, user_id)

    async def _rate_donor(
        self, message: Message, user_id: int, wish_id: int, rating: int
    ) -> None:
        ok = await self.user_storage.wish_rate_donor(wish_id, user_id, rating)
        if ok:
            await render_user_screen(
                message, text=wb_txt.RATING_THANKS_HTML, edit=True
            )
        else:
            await render_user_screen(
                message, text=wb_txt.ERR_RATING_ALREADY, edit=True
            )

    async def _on_admin_callback(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        if not callback.data or not callback.from_user:
            return
        data = callback.data
        admin_id = callback.from_user.id

        if not await self.user_storage.is_telegram_admin_id(admin_id):
            await callback.answer(wb_txt.ADM_NO_ACCESS, show_alert=True)
            return

        if data.startswith(wb_notify.CB_ADM_APPROVE):
            try:
                wish_id = int(data.replace(wb_notify.CB_ADM_APPROVE, ""))
            except ValueError:
                await callback.answer()
                return
            wish = await self.user_storage.wish_approve(wish_id, admin_id)
            await callback.answer(
                wb_txt.ADM_APPROVED_OK if wish else wb_txt.ADM_ALREADY_HANDLED
            )
            if not wish or not self._tg_bot:
                return
            if not (wish.get("button_title") or "").strip():
                title = await generate_wish_button_title(
                    description=str(wish.get("description") or ""),
                    gift_type=str(wish.get("gift_type") or ""),
                )
                await self.user_storage.wish_set_button_title(wish_id, title)
                wish = await self.user_storage.wish_get(wish_id) or wish
            requester = await self.user_storage.get_user(
                int(wish["requester_user_id"])
            )
            await wb_notify.edit_moderation_resolved(
                self._tg_bot,
                wish=wish,
                resolved_label=wb_txt.ADM_RESOLVED_APPROVED,
                requester=requester,
                original_message=callback.message,
                admin=callback.from_user,
            )
            await wb_notify.notify_user_html(
                self._tg_bot,
                int(wish["requester_user_id"]),
                wb_txt.NOTIFY_APPROVED_REQUESTER_HTML,
            )
            try:
                await self.user_storage.wish_log_event_public(
                    wish_id,
                    None,
                    "wb_status_notified",
                    {"status": "approved"},
                )
            except Exception:
                pass
            await wb_notify.post_digest_items(self._tg_bot, self.user_storage, [wish])
            return

        if data.startswith(wb_notify.CB_ADM_REJECT):
            try:
                wish_id = int(data.replace(wb_notify.CB_ADM_REJECT, ""))
            except ValueError:
                await callback.answer()
                return
            wish = await self.user_storage.wish_get(wish_id)
            if not wish or wish.get("status") != "pending_moderation":
                await callback.answer(wb_txt.ADM_ALREADY_HANDLED, show_alert=True)
                return
            await state.set_state(WishBoardAdminStates.waiting_reject_reason)
            await state.update_data(wb_reject_wish_id=wish_id, wb_reject_admin_id=admin_id)
            await callback.answer()
            if self._tg_bot and callback.message:
                await wb_notify.clear_moderation_buttons(self._tg_bot, callback.message)
                await callback.message.reply(
                    wb_txt.ADM_REJECT_PROMPT.format(wish_id=wish_id)
                )

    async def _admin_reject_reason(
        self, message: Message, state: FSMContext
    ) -> None:
        reason = (message.text or "").strip()
        if len(reason) < 3:
            await message.reply(wb_txt.ADM_REJECT_REASON_SHORT)
            return
        data = await state.get_data()
        wish_id = int(data.get("wb_reject_wish_id") or 0)
        admin_id = int(data.get("wb_reject_admin_id") or message.from_user.id)
        await state.clear()
        if not wish_id:
            return
        wish = await self.user_storage.wish_reject(wish_id, admin_id, reason)
        if not wish or not self._tg_bot:
            await message.reply(wb_txt.ADM_REJECT_FAILED)
            return
        requester = await self.user_storage.get_user(int(wish["requester_user_id"]))
        await wb_notify.edit_moderation_resolved(
            self._tg_bot,
            wish=wish,
            resolved_label=wb_txt.ADM_RESOLVED_REJECTED,
            requester=requester,
            admin=message.from_user,
            extra=reason,
        )
        await wb_notify.notify_user_html(
            self._tg_bot,
            int(wish["requester_user_id"]),
            wb_txt.reject_notify_html(reason),
        )
        await message.reply(wb_txt.ADM_REJECT_DONE.format(wish_id=wish_id))

    async def _cron_hourly(self) -> None:
        if not self._tg_bot:
            return
        bot = self._tg_bot
        hours_timeout = config.WISH_BOARD_TAKEN_TIMEOUT_HOURS

        expired = await self.user_storage.wish_expire_open()
        for w in expired:
            await wb_notify.post_admin_lifecycle(
                bot, event=wb_txt.ADM_EVENT_EXPIRED, wish=w
            )
            await self._edit_digest(w)

        released = await self.user_storage.wish_release_stale_taken(hours_timeout)
        for w in released:
            await wb_notify.post_admin_lifecycle(
                bot,
                event=wb_txt.admin_event_taken_timeout(hours_timeout),
                wish=w,
            )
            req_id = int(w["requester_user_id"])
            await wb_notify.notify_user_html(
                bot,
                req_id,
                wb_txt.NOTIFY_TAKEN_TIMEOUT_HTML,
            )
            old_donor = w.get("old_donor_user_id")
            if old_donor:
                await wb_notify.notify_user_html(
                    bot,
                    int(old_donor),
                    wb_txt.donor_timeout_returned_html(w),
                )
            await self._edit_digest(w)

        to_remind = await self.user_storage.wish_list_taken_needing_donor_remind(
            config.WISH_BOARD_DONOR_REMIND_HOURS
        )
        for w in to_remind:
            donor_id = w.get("donor_user_id")
            if not donor_id:
                continue
            wid = int(w["id"])
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=wb_txt.BTN_DONE_MARK,
                            callback_data=f"{CB_DONE_PREFIX}{wid}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=wb_txt.BTN_RETURN_POOL,
                            callback_data=f"{CB_RELEASE_PREFIX}{wid}",
                        )
                    ],
                ]
            )
            await wb_notify.notify_user_html(
                bot,
                int(donor_id),
                wb_txt.donor_remind_html(w),
                reply_markup=kb,
            )
            await self.user_storage.wish_mark_donor_reminded(wid)

        overdue_h = config.WISH_BOARD_MODERATION_OVERDUE_HOURS
        pending_mods = await self.user_storage.wish_list_pending_moderation_reminders(
            remind_after_hours=config.WISH_BOARD_MODERATION_REMIND_HOURS,
            overdue_after_hours=overdue_h,
        )
        topic_id = config.WISH_BOARD_ADMIN_TOPIC_ID
        for w in pending_mods:
            wid = int(w["id"])
            hours_pending = float(w.get("hours_pending") or 0)
            already_reminded = w.get("moderation_reminded_at") is not None
            is_overdue = hours_pending >= overdue_h
            if is_overdue:
                if await self.user_storage.wish_has_event(
                    wid, "wb_moderation_overdue"
                ):
                    continue
                text = wb_txt.adm_moderation_overdue_html(wid)
            else:
                if already_reminded:
                    continue
                text = wb_txt.adm_moderation_remind_html(wid)
            if topic_id:
                await send_admin_html_message_main_bot(
                    bot,
                    sanitize_telegram_html(text),
                    message_thread_id=topic_id,
                    reply_markup=wb_notify.moderation_keyboard(wid),
                )
            if is_overdue:
                await self.user_storage.wish_log_event_public(
                    wid,
                    None,
                    "wb_moderation_overdue",
                    {"hours": round(hours_pending, 1)},
                )
                if not already_reminded:
                    await self.user_storage.wish_mark_moderation_reminded(wid)
            elif not already_reminded:
                await self.user_storage.wish_mark_moderation_reminded(wid)

        no_donor = await self.user_storage.wish_list_open_needing_no_donor_notify(
            config.WISH_BOARD_NO_DONOR_NOTIFY_HOURS
        )
        for w in no_donor:
            req_id = int(w["requester_user_id"])
            await wb_notify.notify_user_html(
                bot, req_id, wb_txt.NO_DONOR_YET_HTML
            )
            await self.user_storage.wish_mark_no_donor_notified(int(w["id"]))
            try:
                await wb_notify.post_group_reminder_wish(
                    bot, self.user_storage, w
                )
            except Exception as e:
                logger.warning(
                    "wish_board no-donor group reminder wish=%s: %s",
                    w.get("id"),
                    e,
                )

    async def _cron_digest(self) -> None:
        if not self._tg_bot:
            return
        since = self._last_digest_at
        if since is None:
            since = datetime.now(timezone.utc) - timedelta(days=1)
        wishes = await self.user_storage.wish_list_open_for_digest_since(since)
        if wishes:
            await wb_notify.post_digest_items(self._tg_bot, self.user_storage, wishes)
        self._last_digest_at = datetime.now(timezone.utc)

    async def _cron_group_reminder(self) -> None:
        if not self._tg_bot:
            return
        wishes = await self.user_storage.wish_list_open_for_group_reminder(
            open_days=config.WISH_BOARD_GROUP_REMINDER_OPEN_DAYS,
            reminder_gap_days=config.WISH_BOARD_GROUP_REMINDER_GAP_DAYS,
            max_reminders=config.WISH_BOARD_GROUP_REMINDER_MAX,
        )
        if not wishes:
            logger.info("[%s] group reminder: нет просьб для напоминания", self.name)
            return
        sent = 0
        for wish in wishes:
            if await wb_notify.post_group_reminder_wish(
                self._tg_bot, self.user_storage, wish
            ):
                sent += 1
        logger.info(
            "[%s] group reminder: отправлено %s из %s",
            self.name,
            sent,
            len(wishes),
        )
