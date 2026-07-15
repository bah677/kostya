# bot/features/benefit.py
import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.features.base import BaseFeature
from bot.filters import PRIVATE_CHAT_ONLY, PRIVATE_INLINE_CALLBACK_ONLY
from bot.texts import media_file_ids as media_ids
from bot.texts import ru_benefit as benefit_txt
from bot.utils.user_ui import render_user_screen, with_main_menu

if TYPE_CHECKING:
    from bot.features.base import FeatureManager
    from storage.user_storage import UserStorage

# Re-export для followup и payment (обратная совместимость)
BENEFIT_INTRO_TEXT = benefit_txt.BENEFIT_INTRO_TEXT
PROMO_PAYMENT_CALLBACK_408 = benefit_txt.PROMO_PAYMENT_CALLBACK_408
PROMO_PAYMENT_CALLBACK_425 = benefit_txt.PROMO_PAYMENT_CALLBACK_425
PROMO_PAYMENT_CALLBACK_GRATITUDE = benefit_txt.PROMO_PAYMENT_CALLBACK_GRATITUDE

logger = logging.getLogger(__name__)


class BenefitFeature(BaseFeature):
    """Фича «Польза» — показывает текст и кнопку с молитвой."""

    name = "benefit"

    def __init__(
        self,
        user_storage: Optional["UserStorage"] = None,
        feature_manager: Optional["FeatureManager"] = None,
    ):
        super().__init__()
        self.user_storage = user_storage
        self.feature_manager = feature_manager
        self._promo_tasks: set[asyncio.Task] = set()

    async def initialize(self) -> None:
        logger.info(f"[{self.name}] Фича инициализирована")

    async def teardown(self) -> None:
        for task in list(self._promo_tasks):
            task.cancel()
        if self._promo_tasks:
            await asyncio.gather(*self._promo_tasks, return_exceptions=True)
        self._promo_tasks.clear()
        logger.info(f"[{self.name}] Фича остановлена")

    def register_handlers(self, dp: Dispatcher) -> None:
        dp.message.register(self.cmd_benefit, PRIVATE_CHAT_ONLY, Command("benefit"))
        dp.callback_query.register(
            self.send_prayer260408,
            (F.data == benefit_txt.CALLBACK_PRAYER_260408) & PRIVATE_INLINE_CALLBACK_ONLY,
        )
        dp.callback_query.register(
            self.send_prayer260425,
            (F.data == benefit_txt.CALLBACK_PRAYER_260425) & PRIVATE_INLINE_CALLBACK_ONLY,
        )
        dp.callback_query.register(
            self.send_prayer_gratitude,
            (F.data == benefit_txt.CALLBACK_PRAYER_GRATITUDE) & PRIVATE_INLINE_CALLBACK_ONLY,
        )

    def _is_benefit3_start_param(self, param: str) -> bool:
        return (param or "").strip() == benefit_txt.START_PARAM_BENEFIT3

    async def try_deliver_gratitude_from_start(
        self,
        message: Message,
        param: str,
        *,
        is_new_user: bool = False,
    ) -> bool:
        """Deep link ``/start benefit3`` — сразу молитва благодарности, как кнопка в /benefit."""
        if not self._is_benefit3_start_param(param):
            return False
        user = message.from_user
        if user is None or user.is_bot:
            return False
        await self._deliver_gratitude_prayer(
            bot=message.bot,
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            schedule_promo=not is_new_user,
            schedule_onboarding=is_new_user,
        )
        return True

    async def _deliver_gratitude_prayer(
        self,
        *,
        bot: Bot,
        chat_id: int,
        user_id: int,
        schedule_promo: bool = True,
        schedule_onboarding: bool = False,
    ) -> None:
        await bot.send_audio(
            chat_id=chat_id,
            audio=media_ids.PRAYER_GRATITUDE_FILE_ID,
            caption=benefit_txt.PRAYER_GRATITUDE_CAPTION,
            parse_mode=ParseMode.HTML,
        )
        activity_since = datetime.now(timezone.utc)

        if schedule_promo:
            kb = with_main_menu(
                [
                    [
                        InlineKeyboardButton(
                            text=benefit_txt.PROMO_INLINE_BUTTON_LABEL,
                            callback_data=benefit_txt.PROMO_PAYMENT_CALLBACK_GRATITUDE,
                        )
                    ]
                ]
            )
            self._schedule_promo_message(
                bot,
                chat_id,
                benefit_txt.PROMO_TEXT_GRATITUDE,
                kb,
                user_id,
                "Молитва благодарности",
                delay_seconds=benefit_txt.PROMO_AFTER_AUDIO_DELAY_GRATITUDE_SECONDS,
                skip_if_inactive=True,
                activity_since=activity_since,
            )
        if schedule_onboarding:
            self._schedule_onboarding_message(
                bot,
                chat_id,
                user_id,
                delay_seconds=benefit_txt.PROMO_AFTER_AUDIO_DELAY_GRATITUDE_SECONDS,
                skip_if_inactive=True,
                activity_since=activity_since,
            )
        logger.info("🎵 Gratitude prayer audio sent to user %s", user_id)

    async def cmd_benefit(self, message: Message, *, edit: bool = False) -> None:
        keyboard = with_main_menu(
            [
                [
                    InlineKeyboardButton(
                        text=benefit_txt.INLINE_BTN_PRAYER_LABEL,
                        callback_data=benefit_txt.CALLBACK_PRAYER_260408,
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=benefit_txt.INLINE_BTN_PODCAST_LABEL,
                        callback_data=benefit_txt.CALLBACK_PRAYER_260425,
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=benefit_txt.INLINE_BTN_GRATITUDE_LABEL,
                        callback_data=benefit_txt.CALLBACK_PRAYER_GRATITUDE,
                    )
                ],
            ],
            include=True,
        )
        await render_user_screen(
            message,
            text=benefit_txt.BENEFIT_INTRO_TEXT,
            reply_markup=keyboard,
            edit=edit,
            add_main_menu=False,
        )

    def _schedule_promo_message(
        self,
        bot: Bot,
        chat_id: int,
        text: str,
        keyboard: InlineKeyboardMarkup,
        log_user_id: int,
        log_label: str,
        *,
        delay_seconds: int = benefit_txt.PROMO_AFTER_AUDIO_DELAY_SECONDS,
        skip_if_inactive: bool = False,
        activity_since: Optional[datetime] = None,
    ) -> None:
        name = self.name

        async def _run():
            try:
                await asyncio.sleep(delay_seconds)
                if (
                    skip_if_inactive
                    and self.user_storage
                    and activity_since is not None
                ):
                    active = await self.user_storage.user_had_activity_since(
                        log_user_id,
                        activity_since,
                    )
                    if active:
                        logger.info(
                            f"[{name}] Промо после аудио ({log_label}) пропущено — "
                            f"user_id={log_user_id} проявил активность"
                        )
                        return
                await bot.send_message(
                    chat_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML,
                )
                logger.info(
                    f"[{name}] Промо после аудио ({log_label}) отправлено "
                    f"user_id={log_user_id}"
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    f"[{name}] Промо после аудио ({log_label}) не отправлено: {e}"
                )

        task = asyncio.create_task(_run())
        self._promo_tasks.add(task)
        task.add_done_callback(self._promo_tasks.discard)

    def _schedule_onboarding_message(
        self,
        bot: Bot,
        chat_id: int,
        user_id: int,
        *,
        delay_seconds: int,
        skip_if_inactive: bool,
        activity_since: datetime,
    ) -> None:
        name = self.name

        async def _run():
            try:
                await asyncio.sleep(delay_seconds)
                me = await bot.get_me()
                if user_id == me.id or chat_id == me.id:
                    return
                if skip_if_inactive and self.user_storage:
                    active = await self.user_storage.user_had_activity_since(
                        user_id,
                        activity_since,
                    )
                    if active:
                        logger.info(
                            "[%s] Онбординг после benefit3 пропущен — "
                            "user_id=%s проявил активность",
                            name,
                            user_id,
                        )
                        return
                onboarding = (
                    self.feature_manager.get("onboarding")
                    if self.feature_manager
                    else None
                )
                if not onboarding:
                    logger.warning("[%s] OnboardingFeature not found for user %s", name, user_id)
                    return
                await onboarding.deliver_standard_onboarding(bot, chat_id, user_id)
                logger.info(
                    "[%s] Онбординг после benefit3 отправлен user_id=%s",
                    name,
                    user_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    "[%s] Онбординг после benefit3 не отправлен user_id=%s: %s",
                    name,
                    user_id,
                    e,
                )

        task = asyncio.create_task(_run())
        self._promo_tasks.add(task)
        task.add_done_callback(self._promo_tasks.discard)

    async def send_prayer260408(self, callback: CallbackQuery) -> None:
        try:
            await callback.message.answer_audio(
                audio=media_ids.PRAYER_260408_FILE_ID,
                caption=benefit_txt.PRAYER_260408_CAPTION,
                parse_mode=ParseMode.HTML,
            )
            await callback.answer()

            kb = with_main_menu(
                [
                    [
                        InlineKeyboardButton(
                            text=benefit_txt.PROMO_INLINE_BUTTON_LABEL,
                            callback_data=benefit_txt.PROMO_PAYMENT_CALLBACK_408,
                        )
                    ]
                ]
            )
            self._schedule_promo_message(
                callback.bot,
                callback.message.chat.id,
                benefit_txt.PROMO_TEXT_408,
                kb,
                callback.from_user.id,
                "Молитва",
            )

            logger.info(f"🎵 Prayer audio sent to user {callback.from_user.id}")
        except Exception as e:
            logger.error(f"❌ Failed to send prayer audio: {e}")
            await callback.answer(benefit_txt.PRAYER_SEND_ERROR)

    async def send_prayer260425(self, callback: CallbackQuery) -> None:
        try:
            await callback.message.answer_audio(
                audio=media_ids.PRAYER_260425_FILE_ID,
                caption=benefit_txt.PRAYER_260425_CAPTION,
                parse_mode=ParseMode.HTML,
            )
            await callback.answer()

            kb = with_main_menu(
                [
                    [
                        InlineKeyboardButton(
                            text=benefit_txt.PROMO_INLINE_BUTTON_LABEL,
                            callback_data=benefit_txt.PROMO_PAYMENT_CALLBACK_425,
                        )
                    ]
                ]
            )
            self._schedule_promo_message(
                callback.bot,
                callback.message.chat.id,
                benefit_txt.PROMO_TEXT_425,
                kb,
                callback.from_user.id,
                "Подкаст",
            )

            logger.info(f"🎵 Prayer audio sent to user {callback.from_user.id}")
        except Exception as e:
            logger.error(f"❌ Failed to send prayer audio: {e}")
            await callback.answer(benefit_txt.PODCAST_SEND_ERROR)

    async def send_prayer_gratitude(self, callback: CallbackQuery) -> None:
        try:
            await self._deliver_gratitude_prayer(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                user_id=callback.from_user.id,
            )
            await callback.answer()
        except Exception as e:
            logger.error(f"❌ Failed to send gratitude prayer audio: {e}")
            await callback.answer(benefit_txt.GRATITUDE_SEND_ERROR)


def build_benefit3_deeplink(bot_username: str) -> str:
    """Ссылка: сразу молитва благодарности (бенефит 3), как кнопка в /benefit."""
    username = (bot_username or "").lstrip("@")
    return f"https://t.me/{username}?start={benefit_txt.START_PARAM_BENEFIT3}"
