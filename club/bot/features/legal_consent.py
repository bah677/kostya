"""Экран однократного согласия с юридическими документами."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Union

from aiogram import Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, User

from bot.features.base import BaseFeature
from bot.filters import PRIVATE_INLINE_CALLBACK_ONLY
from bot.services.legal_documents import LegalDocKind, configured_legal_pdf_file_id
from bot.states import LegalConsentStates
from bot.texts import ru_legal_consent as legal_txt
from bot.utils.inline_buttons import callback_button
from config import config

logger = logging.getLogger(__name__)

CALLBACK_ACCEPT = "legal:accept"
CALLBACK_DOC_OFFER = "legal:doc:offer"
CALLBACK_DOC_POLICY = "legal:doc:policy"
CALLBACK_DOC_CONSENT = "legal:doc:consent"

LEGAL_RESUME_KEY = "legal_resume"

_DOC_CAPTIONS = {
    "offer": legal_txt.OFFER_PDF_CAPTION,
    "policy": legal_txt.POLICY_PDF_CAPTION,
    "consent": legal_txt.CONSENT_PDF_CAPTION,
}


def _user_snapshot(user: User) -> dict:
    try:
        return user.model_dump(mode="json")
    except Exception:
        return {
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "language_code": user.language_code,
            "is_premium": user.is_premium,
            "is_bot": user.is_bot,
        }


class LegalConsentFeature(BaseFeature):
    name = "legal_consent"

    def __init__(self, user_storage, feature_manager=None, bot=None):
        super().__init__()
        self.user_storage = user_storage
        self.feature_manager = feature_manager
        self.bot = bot

    def set_bot(self, bot) -> None:
        self.bot = bot

    async def initialize(self) -> None:
        logger.info("[%s] Фича инициализирована", self.name)

    async def teardown(self) -> None:
        logger.info("[%s] Фича остановлена", self.name)

    def register_handlers(self, dp: Dispatcher) -> None:
        dp.callback_query.register(
            self.handle_callback,
            F.data.startswith("legal:") & PRIVATE_INLINE_CALLBACK_ONLY,
        )

    async def has_consent(self, user_id: int) -> bool:
        return await self.user_storage.has_user_legal_consent(user_id)

    async def ensure_consent_or_prompt(
        self,
        target: Union[Message, CallbackQuery],
        state: FSMContext,
        *,
        source: str,
        resume: Dict[str, Any],
    ) -> bool:
        """True — можно продолжать сценарий; False — показан экран согласия."""
        user = target.from_user
        if user is None:
            return True
        if await self.has_consent(user.id):
            return True
        current = await state.get_state()
        if current and "LegalConsentStates" in str(current):
            return False
        await self._show_consent_screen(target, state, source=source, resume=resume)
        return False

    def _build_consent_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    callback_button(
                        legal_txt.BTN_ACCEPT,
                        CALLBACK_ACCEPT,
                        style="success",
                    )
                ],
                [
                    callback_button(legal_txt.BTN_OFFER, CALLBACK_DOC_OFFER),
                    callback_button(legal_txt.BTN_POLICY, CALLBACK_DOC_POLICY),
                    callback_button(legal_txt.BTN_CONSENT, CALLBACK_DOC_CONSENT),
                ],
            ]
        )

    async def prompt_after_successful_payment(
        self,
        *,
        user_id: int,
        source: str = "payment_success",
    ) -> bool:
        """Показывает согласие отдельным сообщением после успешной первой оплаты."""
        if not self.bot or user_id <= 0:
            return False
        if await self.has_consent(user_id):
            return False
        try:
            await self.bot.send_message(
                user_id,
                legal_txt.POST_PAYMENT_CONSENT_MESSAGE,
                reply_markup=self._build_consent_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            logger.info("[%s] post-payment consent prompt sent user=%s", self.name, user_id)
            return True
        except TelegramForbiddenError:
            await self._mark_user_inactive(user_id)
            logger.info(
                "[%s] post-payment consent skipped — user %s blocked the bot",
                self.name,
                user_id,
            )
            return False
        except Exception as e:
            logger.warning(
                "[%s] failed to send post-payment consent to user %s: %s",
                self.name,
                user_id,
                e,
            )
            return False

    async def _show_consent_screen(
        self,
        target: Union[Message, CallbackQuery],
        state: FSMContext,
        *,
        source: str,
        resume: Dict[str, Any],
    ) -> None:
        keyboard = self._build_consent_keyboard()
        await state.set_state(LegalConsentStates.waiting_accept)
        await state.update_data(
            legal_source=source,
            **{LEGAL_RESUME_KEY: resume},
        )

        message = target if isinstance(target, Message) else target.message
        if message is None:
            return
        try:
            await message.answer(
                legal_txt.CONSENT_MESSAGE,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
        except TelegramForbiddenError:
            user = target.from_user
            if user is not None:
                await self._mark_user_inactive(user.id)
            logger.info(
                "[%s] consent screen skipped — user %s blocked the bot",
                self.name,
                getattr(user, "id", "?"),
            )

    async def handle_callback(self, callback: CallbackQuery, state: FSMContext) -> None:
        data = callback.data or ""
        if data == CALLBACK_ACCEPT:
            await self._handle_accept(callback, state)
            return
        if data in (CALLBACK_DOC_OFFER, CALLBACK_DOC_POLICY, CALLBACK_DOC_CONSENT):
            kind: LegalDocKind = data.rsplit(":", 1)[-1]  # type: ignore[assignment]
            await self._send_document(callback, kind)
            return
        await callback.answer()

    async def _handle_accept(self, callback: CallbackQuery, state: FSMContext) -> None:
        user = callback.from_user
        if user is None or callback.message is None:
            await callback.answer()
            return

        fsm = await state.get_data()
        source = str(fsm.get("legal_source") or "unknown")
        resume = dict(fsm.get(LEGAL_RESUME_KEY) or {})

        await self._record_consent(
            user,
            callback,
            source=source,
        )
        await state.clear()
        await callback.answer()

        action = resume.get("action")
        if action == "onboarding":
            await self._resume_onboarding(callback.message, state, resume)
        elif action == "payment":
            await self._resume_payment(callback, state, resume)
        elif action == "nastya_onboarding":
            await self._resume_nastya_onboarding(callback.message, state, resume)
        else:
            await callback.message.answer(
                legal_txt.CONSENT_ACCEPTED_MESSAGE,
                parse_mode=ParseMode.HTML,
            )
            logger.info("[%s] consent accepted without resume user=%s", self.name, user.id)

    async def _record_consent(
        self,
        user: User,
        callback: CallbackQuery,
        *,
        source: str,
    ) -> None:
        chat = callback.message.chat if callback.message else None
        chat_json = None
        if chat is not None:
            try:
                chat_json = chat.model_dump(mode="json")
            except Exception:
                chat_json = {"id": chat.id, "type": str(chat.type)}

        await self.user_storage.record_user_legal_consent(
            user.id,
            source=source,
            bot_variant=config.BOT_VARIANT,
            telegram_user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            language_code=user.language_code,
            is_premium=user.is_premium,
            is_bot=user.is_bot,
            chat_id=chat.id if chat else None,
            chat_type=str(chat.type) if chat else None,
            message_id=callback.message.message_id if callback.message else None,
            callback_query_id=callback.id,
            inline_message_id=callback.inline_message_id,
            raw_user_json=_user_snapshot(user),
            raw_chat_json=chat_json,
        )

    async def _send_document(self, callback: CallbackQuery, kind: LegalDocKind) -> None:
        fid = configured_legal_pdf_file_id(kind)
        if not fid or callback.message is None:
            await callback.answer(legal_txt.DOC_NOT_CONNECTED_ALERT, show_alert=True)
            return
        try:
            await callback.message.answer_document(
                document=fid,
                caption=_DOC_CAPTIONS[kind],
                parse_mode=ParseMode.HTML,
            )
            await callback.answer()
        except TelegramBadRequest as e:
            logger.warning("[%s] send %s pdf failed: %s", self.name, kind, e.message)
            await callback.answer(legal_txt.DOC_SEND_FAILED_ALERT, show_alert=True)
        except Exception as e:
            logger.warning("[%s] send %s pdf failed: %s", self.name, kind, e)
            await callback.answer(legal_txt.DOC_SEND_FAILED_ALERT, show_alert=True)

    async def _mark_user_inactive(self, user_id: int) -> None:
        try:
            async with self.user_storage.get_connection() as conn:
                try:
                    await conn.execute(
                        """
                        UPDATE users
                        SET is_active = FALSE, updated_at = NOW()
                        WHERE user_id = $1 AND is_active = TRUE
                        """,
                        user_id,
                    )
                except Exception:
                    await conn.execute(
                        """
                        UPDATE users
                        SET is_active = FALSE
                        WHERE user_id = $1 AND is_active = TRUE
                        """,
                        user_id,
                    )
        except Exception as e:
            logger.warning("[%s] failed to mark user %s inactive: %s", self.name, user_id, e)

    async def _resume_onboarding(
        self, message: Message, state: FSMContext, resume: dict
    ) -> None:
        onboarding = self.feature_manager.get("onboarding") if self.feature_manager else None
        if not onboarding:
            return
        await onboarding.continue_start_after_consent(
            message,
            state,
            start_args=resume.get("start_args"),
            is_new_user=bool(resume.get("is_new_user")),
        )

    async def _resume_payment(
        self, callback: CallbackQuery, state: FSMContext, resume: dict
    ) -> None:
        payment = self.feature_manager.get("payment") if self.feature_manager else None
        if not payment:
            return
        await payment.show_tariffs(
            callback,
            is_gift=bool(resume.get("is_gift", False)),
            tariff_type=str(resume.get("tariff_type") or "base"),
            show_gift_button=bool(resume.get("show_gift_button", True)),
            skip_consent_check=True,
        )

    async def _resume_nastya_onboarding(
        self, message: Message, state: FSMContext, resume: dict
    ) -> None:
        nastya = (
            self.feature_manager.get("nastya_temp_onboarding")
            if self.feature_manager
            else None
        )
        if not nastya:
            return
        await nastya.continue_after_consent(message, state)
