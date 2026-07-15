# bot/features/onboarding.py
"""Онбординг: /start, сохранение пользователя, gift/ref/promo deep links, приветствие."""

import logging
from datetime import datetime

from aiogram import Dispatcher
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram import Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from bot.features.base import BaseFeature
from bot.texts import media_file_ids as media_ids
from bot.texts import ru_onboarding as onb_txt
from bot.utils.user_ui import with_main_menu

logger = logging.getLogger(__name__)

# Re-export для внешних импортов (обратная совместимость)
VIDEO_CIRCLE_FILE_ID = media_ids.VIDEO_CIRCLE_FILE_ID
TOPIC_BTN_MONEY = onb_txt.TOPIC_BTN_MONEY
TOPIC_BTN_RELATIONS = onb_txt.TOPIC_BTN_RELATIONS
CALLBACK_TOPIC_MONEY = onb_txt.CALLBACK_TOPIC_MONEY
CALLBACK_TOPIC_RELATIONS = onb_txt.CALLBACK_TOPIC_RELATIONS


def _extract_start_payload(message: Message, start_args_from_command: str | None) -> str | None:
    """Payload deep link после ``/start`` (надёжнее, чем ``text.split()``)."""
    if start_args_from_command is not None:
        s = start_args_from_command.strip()
        return s if s else None
    raw = (message.text or "").strip()
    if not raw:
        return None
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        return None
    tail = parts[1].strip()
    return tail if tail else None


def _onboarding_start_messages() -> tuple[str, ...]:
    """Сообщения после кружков; str в конфиге трактуем как одно сообщение (частая ошибка без запятой)."""
    raw = getattr(onb_txt, "ONBOARDING_START_MESSAGES", ()) or ()
    if isinstance(raw, str):
        return (raw,)
    return tuple(raw)


def _onboarding_video_note_file_ids() -> tuple[str, ...]:
    """Непустые file_id кружков из media_file_ids (twin может задать свой список)."""
    raw = getattr(media_ids, "ONBOARDING_VIDEO_NOTE_FILE_IDS", None)
    if raw:
        return tuple(str(x).strip() for x in raw if x and str(x).strip())
    legacy = (getattr(media_ids, "VIDEO_CIRCLE_FILE_ID", None) or "").strip()
    return (legacy,) if legacy else ()


class OnboardingFeature(BaseFeature):
    """Фича приветствия по /start (команда регистрируется в ``CommandHandlers``)."""

    name = "onboarding"

    def __init__(
        self,
        user_storage,
        openai_client,
        feature_manager=None,
        message_copier=None,
        interaction_logger=None,
    ):
        super().__init__()
        self.user_storage = user_storage
        self.openai_client = openai_client
        self.feature_manager = feature_manager
        self.message_copier = message_copier
        self.interaction_logger = interaction_logger
        self.bot = None

    def set_bot(self, bot):
        """Устанавливает экземпляр бота."""
        self.bot = bot

    async def initialize(self) -> None:
        logger.info(f"[{self.name}] Фича инициализирована")

    async def teardown(self) -> None:
        logger.info(f"[{self.name}] Фича остановлена")

    def register_handlers(self, _dp: Dispatcher) -> None:
        """Команда ``/start`` регистрируется в ``bot/handlers/commands.py`` (без дубля)."""

    async def cmd_start(
        self,
        message: Message,
        state: FSMContext,
        *,
        start_args: str | None = None,
    ):
        """Логика /start (вызывается из CommandHandlers)."""
        user_id = message.from_user.id
        param = _extract_start_payload(message, start_args)

        existing_user = await self.user_storage.get_user(user_id)
        is_new_user = existing_user is None

        await self.user_storage.save_user_from_message(message)

        from bot.services.attribution_touch import parse_start_payload, parse_start_text

        touch = parse_start_payload(param) if param else None
        if not touch and message.text:
            touch = parse_start_text(message.text)
        if touch:
            await self.user_storage.record_attribution_touch(
                user_id, touch, source_type="start"
            )
            from bot.services.ref_key_registry import maybe_alert_new_marketing_touch

            await maybe_alert_new_marketing_touch(self.user_storage, self.bot, touch)

        if param:
            from bot.services.promo_campaign_service import assign_promo_from_start_param

            assigned = await assign_promo_from_start_param(
                self.user_storage, user_id, param
            )
            if assigned:
                logger.info(
                    "[%s] Promo campaign assigned user_id=%s guid=%s name=%r",
                    self.name,
                    user_id,
                    assigned.get("campaign_guid"),
                    assigned.get("name"),
                )

        logger.info(
            "[%s] /start user_id=%s payload=%r raw_text=%r",
            self.name,
            user_id,
            param,
            message.text,
        )

        await self.continue_start_after_consent(
            message, state, start_args=param, is_new_user=is_new_user
        )

    async def continue_start_after_consent(
        self,
        message: Message,
        state: FSMContext,
        *,
        start_args: str | None = None,
        is_new_user: bool = False,
    ) -> None:
        """Продолжение /start после согласия с документами (или если согласие уже есть)."""
        user_id = message.from_user.id
        param = start_args

        if param and self.feature_manager:
            benefit = self.feature_manager.get("benefit")
            if benefit and await benefit.try_deliver_gratitude_from_start(
                message, param, is_new_user=is_new_user
            ):
                if is_new_user:
                    followup = self.feature_manager.get("followup")
                    if followup:
                        await followup.on_start(
                            user_id, is_new_user=True, start_param=param
                        )
                await state.clear()
                logger.info(
                    "[%s] Benefit3 deep link handled for user %s param=%r",
                    self.name,
                    user_id,
                    param,
                )
                return

        # Промо по deep link до ref/gift и до «кружка», чтобы ссылка попадала в нужный сценарий
        if param and self.feature_manager:
            payment = self.feature_manager.get("payment")
            if payment and await payment.try_show_promo_tariffs_from_start(
                message, param, state=state
            ):
                if is_new_user:
                    followup = self.feature_manager.get("followup")
                    if followup:
                        await followup.on_start(
                            user_id, is_new_user=True, start_param=param
                        )
                await state.clear()
                logger.info(
                    "[%s] Promo deep link handled for user %s param=%r",
                    self.name,
                    user_id,
                    param,
                )
                return

        if param and self.feature_manager:
            wish_board = self.feature_manager.get("wish_board")
            if wish_board and await wish_board.try_open_from_start(
                message, state, param
            ):
                if is_new_user:
                    followup = self.feature_manager.get("followup")
                    if followup:
                        await followup.on_start(
                            user_id, is_new_user=True, start_param=param
                        )
                await state.clear()
                logger.info(
                    "[%s] Wish board deep link handled for user %s param=%r",
                    self.name,
                    user_id,
                    param,
                )
                return

        if param:
            if param.startswith("gift_"):
                gift_code = param[5:]
                gift_feature = self.feature_manager.get("gift_activation")
                if gift_feature:
                    await gift_feature.activate_gift(message, gift_code)
                else:
                    logger.warning("GiftActivationFeature not found")

            elif param.startswith("ref_"):
                referrer_id = param[4:]
                referral_feature = self.feature_manager.get("referral")
                if referral_feature:
                    await referral_feature.register_referral(
                        message, referrer_id, is_new_user
                    )
                else:
                    logger.warning("ReferralFeature not found")

        try:
            await self._send_onboarding_content(message, user_id)
        except Exception as e:
            logger.error("❌ Failed to send onboarding content: %s", e)

        if self.feature_manager and is_new_user:
            followup = self.feature_manager.get("followup")
            if followup:
                await followup.on_start(
                    user_id, is_new_user=True, start_param=param
                )

        await state.clear()

    async def _send_onboarding_content(self, message: Message, user_id: int) -> None:
        """Кружки и тексты по конфигу twin (media_file_ids + ru_onboarding)."""
        await self.deliver_standard_onboarding(
            message.bot, message.chat.id, user_id
        )

    async def deliver_standard_onboarding(
        self, bot: Bot, chat_id: int, user_id: int
    ) -> None:
        """Стандартный онбординг: 4 кружка, тексты после них, welcome."""
        video_ids = _onboarding_video_note_file_ids()
        for i, file_id in enumerate(video_ids, start=1):
            await self._send_one_video_note(
                bot,
                chat_id,
                user_id,
                file_id,
                index=i,
                total=len(video_ids),
            )

        for text in _onboarding_start_messages():
            body = (text or "").strip()
            if not body:
                continue
            await bot.send_message(chat_id, body, parse_mode=ParseMode.HTML)
            logger.info("[%s] start message sent user=%s", self.name, user_id)

        if not getattr(onb_txt, "ONBOARDING_SEND_LICENSE_WELCOME", True) and not getattr(
            onb_txt, "ONBOARDING_SEND_NO_LICENSE_WELCOME", True
        ):
            return

        license_info = await self.user_storage.get_user_active_license(user_id)
        now = datetime.now()
        has_license = license_info and license_info["expires_at"] > now

        if has_license and getattr(onb_txt, "ONBOARDING_SEND_LICENSE_WELCOME", True):
            await self._show_welcome_with_group(user_id, bot, chat_id, license_info)
        elif not has_license and getattr(onb_txt, "ONBOARDING_SEND_NO_LICENSE_WELCOME", True):
            await self._show_welcome_without_license(bot, chat_id, user_id)

    async def _send_one_video_note(
        self,
        bot: Bot,
        chat_id: int,
        user_id: int,
        file_id: str,
        *,
        index: int,
        total: int,
    ) -> None:
        try:
            await bot.send_video_note(chat_id=chat_id, video_note=file_id)
            logger.info(
                "✅ Video note %s/%s sent to user %s",
                index,
                total,
                user_id,
            )
        except TelegramBadRequest as e:
            err = str(e)
            if (
                "VOICE_MESSAGES_FORBIDDEN" in err
                or "voice_messages_forbidden" in err.lower()
            ):
                logger.warning(
                    "Video note %s/%s недоступен для user %s: %s",
                    index,
                    total,
                    user_id,
                    err,
                )
                return
            logger.error("Video note %s/%s TelegramBadRequest: %s", index, total, e)
        except Exception as e:
            logger.error("Video note %s/%s error: %s", index, total, e)

    async def _show_welcome_with_group(
        self,
        user_id: int,
        bot: Bot,
        chat_id: int,
        license_info: dict,
    ) -> None:
        """Приветствие для пользователя с активной подпиской; кнопка — только если есть ссылка."""
        keyboard = None
        link_type: str | None = None

        club_group = self.feature_manager.get("club_group") if self.feature_manager else None
        expires_str = license_info["expires_at"].strftime("%d.%m.%Y")

        if club_group:
            group_link, link_type = await club_group.get_group_link_for_user(user_id)
            if group_link:
                button_text = (
                    onb_txt.BTN_OPEN_GROUP
                    if link_type == "post"
                    else onb_txt.BTN_JOIN_GROUP
                )
                keyboard = with_main_menu(
                    [[InlineKeyboardButton(text=button_text, url=group_link)]]
                )
        else:
            logger.warning("ClubGroupFeature not found")

        welcome_text = onb_txt.welcome_subscribed_html(expires_str=expires_str)
        if keyboard:
            if link_type == "post":
                welcome_text += onb_txt.WELCOME_HINT_OPEN_POST
            else:
                welcome_text += onb_txt.WELCOME_HINT_JOIN
        elif club_group and link_type == "unconfigured":
            welcome_text += onb_txt.WELCOME_HINT_UNCONFIGURED
        else:
            welcome_text += onb_txt.WELCOME_HINT_NO_LINK

        await bot.send_message(
            chat_id,
            welcome_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        logger.info(
            "✅ Welcome (subscribed) sent to user %s (link_type=%s, has_button=%s)",
            user_id,
            link_type,
            bool(keyboard),
        )

    async def _show_welcome_without_license(
        self, bot: Bot, chat_id: int, user_id: int
    ) -> None:
        """Приветствие без активной подписки (без inline-кнопки оплаты — оплата через /payment)."""
        await bot.send_message(
            chat_id, onb_txt.WELCOME_NO_LICENSE_HTML, parse_mode=ParseMode.HTML
        )
        logger.info("✅ Welcome (no license) sent to user %s", user_id)

    async def handle_message(self, message: Message, state: FSMContext, text: str):
        logger.debug(f"[{self.name}] handle_message called (stub)")
        await self.cmd_start(message, state, start_args=None)

    async def handle_callback(self, callback: CallbackQuery, state: FSMContext):
        data = callback.data or ""
        if data == CALLBACK_TOPIC_MONEY:
            text = TOPIC_BTN_MONEY
        elif data == CALLBACK_TOPIC_RELATIONS:
            text = TOPIC_BTN_RELATIONS
        else:
            logger.debug("[%s] handle_callback unknown onboarding data=%s", self.name, data)
            await callback.answer()
            return

        await callback.answer()

        user_id = callback.from_user.id

        chat = callback.message.chat if callback.message else None
        chat_id = chat.id if chat else user_id

        mid: int | None = None
        if self.message_copier:
            mid = await self.message_copier.save_synthetic_private_user_text(
                user_id=user_id,
                chat_id=chat_id,
                content=text,
                callback_query_id=callback.id,
                callback_data=data,
            )

        if self.interaction_logger:
            await self.interaction_logger.log(
                user_id=user_id,
                event_category="message",
                event_type="received_from_inline_button",
                message_id=mid,
                data={
                    "text": text,
                    "mirror_of_manual_reply": True,
                    "callback_data": data,
                },
                chat_id=chat_id,
                chat_type="private",
                callback_data=data,
                source="onboarding_topic_pick",
                outcome="logged_as_user_text_equivalent",
            )

        from bot.media_processing.models import MediaType, ProcessedMedia

        processed = ProcessedMedia(
            text=text,
            media_type=MediaType.TEXT,
            user_id=user_id,
            confidence=1.0,
            has_text=True,
        )

        if self.bot and callback.message:
            await self.bot.add_to_queue(user_id, {
                "message": callback.message,
                "processed": processed,
                "message_id": mid,
                "onboarding_topic_button": True,
            })
            logger.info(
                "[%s] Topic pick queued (same pipeline as typed): user=%s text=%r",
                self.name,
                user_id,
                text,
            )
        elif self.feature_manager and callback.message:
            messaging = self.feature_manager.get("messaging")
            await messaging.handle_chat_message(
                callback.message,
                state,
                text,
                message_id=mid,
                onboarding_topic_button=True,
            )
            logger.warning("[%s] add_to_queue skipped (no bot), sent to messaging directly", self.name)

    async def start_onboarding(
        self,
        user_id: int,
        message: Message,
        state: FSMContext,
        *,
        start_args: str | None = None,
    ):
        await self.cmd_start(message, state, start_args=start_args)
