"""Подарок продления подписки действующему участнику клуба."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from aiogram import Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.features.base import BaseFeature
from bot.filters import PRIVATE_INLINE_CALLBACK_ONLY
from bot.states import MemberGiftExtensionStates
from bot.texts import ru_member_gift as mg_txt
from bot.utils.user_ui import render_user_screen, with_main_menu

logger = logging.getLogger(__name__)

CB_PICK_PREFIX = "mgift_pick:"
CB_CONFIRM_PREFIX = "mgift_confirm:"
CB_TARIFF_PREFIX = "payment_mgift_select_"


class MemberGiftExtensionFeature(BaseFeature):
    name = "member_gift_extension"

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
            self._on_pick_recipient,
            F.data.startswith(CB_PICK_PREFIX),
            PRIVATE_INLINE_CALLBACK_ONLY,
        )
        dp.callback_query.register(
            self._on_confirm_recipient,
            F.data.startswith(CB_CONFIRM_PREFIX),
            PRIVATE_INLINE_CALLBACK_ONLY,
        )

    async def start_flow(
        self, message: Message, state: FSMContext, *, edit: bool = False
    ) -> None:
        await state.set_state(MemberGiftExtensionStates.waiting_recipient_query)
        await render_user_screen(
            message,
            text=mg_txt.PROMPT_RECIPIENT_HTML,
            edit=edit,
        )

    async def start_for_recipient(
        self,
        message: Message,
        state: FSMContext,
        recipient_id: int,
        *,
        edit: bool = False,
        wish_id: Optional[int] = None,
        hide_recipient_identity: bool = False,
    ) -> None:
        """Подарок конкретному участнику без шага поиска (доска желаний)."""
        donor_id = message.from_user.id if message.from_user else None
        if donor_id and recipient_id == donor_id:
            await render_user_screen(message, text=mg_txt.ERR_SELF, edit=edit)
            return
        if not await self.user_storage.user_has_active_license(recipient_id):
            await render_user_screen(message, text=mg_txt.ERR_NOT_FOUND, edit=edit)
            return

        user = await self.user_storage.get_user(recipient_id) or {}
        user["user_id"] = recipient_id
        extra: Dict[str, Any] = {
            "gift_recipient_user_id": recipient_id,
            "gift_recipient_name": "" if hide_recipient_identity else mg_txt.display_name(user),
            "is_member_gift": True,
            "gift_recipient_anonymous": hide_recipient_identity,
        }
        if wish_id is not None:
            extra["wish_board_wish_id"] = wish_id
        await state.update_data(**extra)
        await self._prompt_confirm_recipient(
            message, state, user, edit=edit, hide_recipient_identity=hide_recipient_identity
        )

    async def handle_recipient_query(
        self, message: Message, state: FSMContext, text: str
    ) -> None:
        donor_id = message.from_user.id
        query = (text or "").strip()
        if len(query) < 2:
            await message.answer(mg_txt.ERR_QUERY_TOO_SHORT)
            return

        matches = await self.user_storage.search_active_club_members(
            query, exclude_user_id=donor_id, limit=10
        )
        if not matches:
            await message.answer(mg_txt.ERR_NOT_FOUND)
            return

        if len(matches) == 1:
            await self._prompt_confirm_recipient(
                message, state, matches[0], edit=False
            )
            return

        await render_user_screen(
            message,
            text=mg_txt.PICK_RECIPIENT_HTML,
            reply_markup=self._pick_keyboard(matches),
            edit=False,
            add_main_menu=False,
        )

    async def _prompt_confirm_recipient(
        self,
        message: Message,
        state: FSMContext,
        row: Dict[str, Any],
        *,
        edit: bool = False,
        hide_recipient_identity: bool = False,
    ) -> None:
        recipient_id = int(row["user_id"])
        fsm = await state.get_data()
        hide = hide_recipient_identity or bool(fsm.get("gift_recipient_anonymous"))
        name = mg_txt.display_name(row)
        await state.update_data(
            gift_recipient_user_id=recipient_id,
            gift_recipient_name="" if hide else name,
            is_member_gift=True,
            gift_recipient_anonymous=hide,
        )
        await state.set_state(None)

        kb = with_main_menu(
            [
                [
                    InlineKeyboardButton(
                        text=mg_txt.BTN_CONFIRM,
                        callback_data=f"{CB_CONFIRM_PREFIX}{recipient_id}",
                    )
                ],
            ]
        )
        if hide:
            caption = mg_txt.CONFIRM_RECIPIENT_ANON_HTML
        else:
            caption = (
                f"{mg_txt.CONFIRM_RECIPIENT_HTML}\n\n"
                f"<b>{mg_txt.escape_name(name)}</b>"
            )

        if edit:
            await render_user_screen(
                message,
                text=caption,
                reply_markup=kb,
                edit=True,
                add_main_menu=False,
            )
            return

        if hide:
            await render_user_screen(
                message,
                text=caption,
                reply_markup=kb,
                edit=False,
                add_main_menu=False,
            )
            return

        sent_with_photo = await self._try_send_with_photo(
            message, recipient_id, caption, kb
        )
        if not sent_with_photo:
            await render_user_screen(
                message,
                text=caption,
                reply_markup=kb,
                edit=False,
                add_main_menu=False,
            )

    async def _try_send_with_photo(
        self,
        message: Message,
        user_id: int,
        caption: str,
        kb: InlineKeyboardMarkup,
    ) -> bool:
        if not self.bot:
            return False
        try:
            photos = await self.bot.get_user_profile_photos(user_id, limit=1)
            if not photos.total_count or not photos.photos:
                return False
            file_id = photos.photos[0][-1].file_id
            await message.answer_photo(
                photo=file_id,
                caption=caption,
                reply_markup=kb,
                parse_mode=ParseMode.HTML,
            )
            return True
        except TelegramBadRequest as e:
            logger.debug("mgift photo unavailable uid=%s: %s", user_id, e)
            return False
        except Exception as e:
            logger.warning("mgift photo error uid=%s: %s", user_id, e)
            return False

    def _pick_keyboard(self, matches: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
        rows = []
        for row in matches:
            uid = int(row["user_id"])
            rows.append(
                [
                    InlineKeyboardButton(
                        text=mg_txt.recipient_button_label(row),
                        callback_data=f"{CB_PICK_PREFIX}{uid}",
                    )
                ]
            )
        return with_main_menu(rows)

    async def _on_pick_recipient(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        await callback.answer()
        if not callback.message or not callback.data:
            return
        try:
            recipient_id = int(callback.data.replace(CB_PICK_PREFIX, ""))
        except ValueError:
            return
        if recipient_id == callback.from_user.id:
            await render_user_screen(
                callback.message, text=mg_txt.ERR_SELF, edit=True
            )
            return
        if not await self.user_storage.user_has_active_license(recipient_id):
            await render_user_screen(
                callback.message, text=mg_txt.ERR_NOT_FOUND, edit=True
            )
            return
        user = await self.user_storage.get_user(recipient_id) or {}
        user["user_id"] = recipient_id
        await self._prompt_confirm_recipient(
            callback.message, state, user, edit=True
        )

    async def _on_confirm_recipient(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        await callback.answer()
        if not callback.message or not callback.data:
            return
        try:
            recipient_id = int(callback.data.replace(CB_CONFIRM_PREFIX, ""))
        except ValueError:
            return
        if recipient_id == callback.from_user.id:
            await render_user_screen(
                callback.message, text=mg_txt.ERR_SELF, edit=True
            )
            return
        if not await self.user_storage.user_has_active_license(recipient_id):
            await render_user_screen(
                callback.message, text=mg_txt.ERR_NOT_FOUND, edit=True
            )
            return

        user = await self.user_storage.get_user(recipient_id) or {}
        fsm = await state.get_data()
        hide = bool(fsm.get("gift_recipient_anonymous"))
        name = "" if hide else mg_txt.display_name({**user, "user_id": recipient_id})
        await state.update_data(
            gift_recipient_user_id=recipient_id,
            gift_recipient_name=name,
            is_member_gift=True,
            gift_recipient_anonymous=hide,
        )
        await state.set_state(None)

        payment = self.feature_manager.get("payment")
        if not payment:
            await render_user_screen(
                callback.message,
                text="Оплата временно недоступна.",
                edit=True,
            )
            return
        await payment.show_member_gift_tariffs(
            callback, state, recipient_id, name, recipient_anonymous=hide
        )
