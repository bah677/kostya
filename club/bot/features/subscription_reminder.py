# bot/features/subscription_reminder.py
import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from aiogram import Dispatcher, F
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

from bot.features.base import BaseFeature
from bot.filters import PRIVATE_INLINE_CALLBACK_ONLY
from bot.services.club_removal_card import (
    REASON_BONUS_EXPIRED,
    build_club_removal_card_html,
)
from bot.texts import ru_subscription_reminder as sub_txt
from bot.utils.admin_channel import (
    resolve_admin_service_thread_id,
    send_admin_html_message,
)
from bot.utils.user_ui import render_user_screen, with_main_menu
from config import config

if TYPE_CHECKING:
    from openai import AsyncOpenAI
    from rag.runtime import RagStack

logger = logging.getLogger(__name__)

MSK_TZ = ZoneInfo("Europe/Moscow")
_OUTREACH_SLUG_BONUS = "bonus_extension_plus_one_day"
_OUTREACH_SLUG_POST_BONUS_FINAL = "post_bonus_expiry_final"

CB_REM_AFFILIATE = "rem_affiliate"
CB_CHURN_REPLY = "churn_reply"
CHURN18_PREFIX = "churn18:"

CHURN18_LABEL_BY_KEY = sub_txt.CHURN18_LABEL_BY_KEY


def today_moscow() -> date:
    return datetime.now(MSK_TZ).date()


def outreach_slug_reminder(reminder: Dict[str, Any]) -> str:
    return f"expiry_minus_{reminder['days_before']}d_ord{reminder['order']}"


def payment_cta_button(label: str) -> InlineKeyboardButton:
    """Кнопка оплаты: зелёный стиль клиента Telegram (как в messaging / payment_start)."""
    t = (label or "").strip()
    return InlineKeyboardButton(
        text=t,
        callback_data="payment_start",
        style="success",
    )


# старое имя — для dev/subscription_chain_test и внешних импортов
green_payment_button = payment_cta_button


class SubscriptionReminderFeature(BaseFeature):
    """
    Цепочка напоминаний по ТЗ «цепочка_сообщений_клуб_финал.md».
    Ежедневно 9:00 МСК: до окончания (7/5/3/1), день 0 (+1 день), день +1 (выход),
    затем +5/+10/+18/+30 дней после выхода.
    """

    name = "subscription_reminder"

    REMINDER_CONFIG = sub_txt.REMINDER_CONFIG
    BONUS_CONFIG = sub_txt.BONUS_CONFIG
    REMOVE_CONFIG = sub_txt.REMOVE_CONFIG
    CHURN_MESSAGES = sub_txt.CHURN_MESSAGES

    def __init__(
        self,
        user_storage,
        bot,
        feature_manager=None,
        referral_feature=None,
    ):
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot
        self.feature_manager = feature_manager
        self.referral_feature = referral_feature
        self.scheduler = None
        self._llm_client: Optional["AsyncOpenAI"] = None
        self.rag_stack: Optional["RagStack"] = None

    def set_llm_client(self, client: "AsyncOpenAI") -> None:
        self._llm_client = client

    def set_rag_stack(self, rag_stack: "RagStack") -> None:
        self.rag_stack = rag_stack

    async def initialize(self) -> None:
        if not config.SUBSCRIPTION_REMINDER_ENABLED:
            logger.info(
                "[%s] Выключено (SUBSCRIPTION_REMINDER_ENABLED)",
                self.name,
            )
            return
        logger.info("[%s] Фича инициализирована", self.name)
        self.scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
        self.scheduler.add_job(
            self._process_all,
            CronTrigger(hour=9, minute=0, timezone="Europe/Moscow"),
            id="subscription_reminder_daily",
        )
        self.scheduler.start()
        logger.info("[%s] Планировщик: каждый день 9:00 МСК", self.name)

    async def teardown(self) -> None:
        if self.scheduler:
            self.scheduler.shutdown()
        logger.info("[%s] Фича остановлена", self.name)

    def register_handlers(self, dp: Dispatcher) -> None:
        dp.callback_query.register(
            self._on_rem_affiliate,
            F.data == CB_REM_AFFILIATE,
            PRIVATE_INLINE_CALLBACK_ONLY,
        )
        dp.callback_query.register(
            self._on_churn_reply,
            F.data == CB_CHURN_REPLY,
            PRIVATE_INLINE_CALLBACK_ONLY,
        )
        dp.callback_query.register(
            self._on_churn18_feedback,
            F.data.startswith(CHURN18_PREFIX),
            PRIVATE_INLINE_CALLBACK_ONLY,
        )

    def _keyboard_reminder(self, kind: str) -> Optional[InlineKeyboardMarkup]:
        rows: Optional[List[List[InlineKeyboardButton]]] = None
        if kind == "payment_extend":
            rows = [[payment_cta_button(sub_txt.BTN_PAYMENT_EXTEND)]]
        elif kind == "affiliate_and_extend":
            rows = [
                [
                    InlineKeyboardButton(
                        text=sub_txt.BTN_AFFILIATE_LINK,
                        callback_data=CB_REM_AFFILIATE,
                    )
                ],
                [payment_cta_button(sub_txt.BTN_PAYMENT_EXTEND)],
            ]
        elif kind == "return_club":
            rows = [[payment_cta_button(sub_txt.BTN_RETURN_CLUB)]]
        elif kind == "churn_reply":
            rows = [
                [
                    InlineKeyboardButton(
                        text=sub_txt.BTN_CHURN_REPLY,
                        callback_data=CB_CHURN_REPLY,
                    )
                ]
            ]
        elif kind == "churn18":
            rows = []
            for key in ("1", "2", "3", "4", "5", "6"):
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=sub_txt.churn18_button_label(key),
                            callback_data=f"{CHURN18_PREFIX}{key}",
                        )
                    ]
                )
            rows.append([payment_cta_button(sub_txt.BTN_RETURN_CLUB)])
        if not rows:
            return None
        return with_main_menu(rows)

    async def _process_all(self):
        today_msk = today_moscow()
        logger.info("📅 subscription_reminder: дата МСК %s", today_msk)
        await self._process_reminders(today_msk)
        await self._process_bonus_extensions(today_msk)
        await self._process_expired_and_remove(today_msk)
        await self._process_churn_outreach(today_msk)

    async def _process_reminders(self, today_msk: date):
        ordered = sorted(
            self.REMINDER_CONFIG,
            key=lambda x: (-x["days_before"], x["order"]),
        )
        for reminder in ordered:
            days_before = reminder["days_before"]
            target_date = today_msk + timedelta(days=days_before)
            slug = outreach_slug_reminder(reminder)

            licenses = await self.user_storage.get_active_subscriptions_expiring_on(
                target_date
            )

            for lic in licenses:
                uid = lic["user_id"]
                from bot.utils.admin_outreach_skip import (
                    should_skip_subscription_outreach_slug,
                )

                if await should_skip_subscription_outreach_slug(
                    self.user_storage, uid, slug
                ):
                    continue
                if (
                    reminder.get("keyboard") == "affiliate_and_extend"
                    and await self.user_storage.is_telegram_admin_id(uid)
                ):
                    continue
                first_name = lic.get("first_name")
                claimed = await self.user_storage.try_claim_subscription_outreach(
                    uid, slug, today_msk
                )
                if not claimed:
                    continue

                if config.MEMBER_RENEWAL_AI_ENABLED and self._llm_client:
                    from bot.services.member_renewal_outreach import (
                        generate_renewal_outreach_html,
                    )

                    body = await generate_renewal_outreach_html(
                        user_storage=self.user_storage,
                        llm_client=self._llm_client,
                        rag_stack=self.rag_stack,
                        user_id=uid,
                        first_name=first_name,
                        reminder=reminder,
                        license_expires_at=lic.get("expires_at"),
                    )
                    await self.user_storage.set_member_renewal_state(
                        uid, f"reminder_{days_before}d"
                    )
                else:
                    body = sub_txt.personalize_html(reminder["text"], first_name)

                kb = self._keyboard_reminder(reminder["keyboard"])
                ok = await self._send_html(uid, body, kb)
                if ok:
                    logger.info(
                        "📨 Reminder slug=%s days_before=%s user=%s",
                        slug,
                        days_before,
                        uid,
                    )
                await asyncio.sleep(0.5)

    async def _process_bonus_extensions(self, today_msk: date):
        yesterday = today_msk - timedelta(days=1)
        licenses = await self.user_storage.get_expired_subscriptions_for_bonus(
            yesterday
        )

        for lic in licenses:
            uid = lic["user_id"]
            from bot.utils.admin_outreach_skip import (
                should_skip_subscription_outreach_slug,
            )

            if await should_skip_subscription_outreach_slug(
                self.user_storage, uid, _OUTREACH_SLUG_BONUS
            ):
                continue
            claimed = await self.user_storage.try_claim_subscription_outreach(
                uid, _OUTREACH_SLUG_BONUS, today_msk
            )
            if not claimed:
                continue

            new_expiry = datetime.now(MSK_TZ) + timedelta(
                days=self.BONUS_CONFIG["bonus_days"]
            )

            converted = await self.user_storage.convert_to_bonus_license(uid, new_expiry)
            if not converted:
                logger.error(
                    "❌ convert_to_bonus_license uid=%s — нет активной лицензии или БД",
                    uid,
                )
                continue

            fn = await self._first_name(uid)
            msg = sub_txt.personalize_html(self.BONUS_CONFIG["message"], fn)
            kb = with_main_menu(
                [[payment_cta_button(self.BONUS_CONFIG["button"])]]
            )
            await self._send_html(uid, msg, kb)
            logger.info(
                "🎁 Bonus extension uid=%s until %s",
                uid,
                new_expiry.date(),
            )
            await asyncio.sleep(0.5)

    async def _first_name(self, user_id: int) -> Optional[str]:
        info = await self.user_storage.get_user(user_id)
        if not info:
            return None
        return info.get("first_name")

    async def _process_expired_and_remove(self, today_msk: date):
        yesterday = today_msk - timedelta(days=1)
        licenses = await self.user_storage.get_expired_bonus_licenses(yesterday)

        club_group = (
            self.feature_manager.get("club_group") if self.feature_manager else None
        )
        club_gid = config.CLUB_GROUP_ID

        for lic in licenses:
            user_id = lic["user_id"]
            from bot.utils.admin_outreach_skip import (
                should_skip_subscription_outreach_slug,
            )

            if await should_skip_subscription_outreach_slug(
                self.user_storage, user_id, _OUTREACH_SLUG_POST_BONUS_FINAL
            ):
                continue

            claimed = await self.user_storage.try_claim_subscription_outreach(
                user_id, _OUTREACH_SLUG_POST_BONUS_FINAL, today_msk
            )
            if not claimed:
                continue

            fn = await self._first_name(user_id)
            msg = sub_txt.personalize_html(self.REMOVE_CONFIG["message"], fn)
            kb = with_main_menu(
                [[payment_cta_button(self.REMOVE_CONFIG["button"])]]
            )
            await self._send_html(user_id, msg, kb)

            if club_gid and club_group:
                try:
                    await self.bot.ban_chat_member(chat_id=club_gid, user_id=user_id)
                    await self.bot.unban_chat_member(chat_id=club_gid, user_id=user_id)
                    logger.info("🚪 User %s removed from group %s", user_id, club_gid)
                    await self.user_storage.record_club_member_exclusion(
                        user_id,
                        reason="bonus_expired",
                        source="subscription_reminder",
                    )
                except Exception as e:
                    logger.error("❌ kick uid=%s: %s", user_id, e)
            else:
                logger.info(
                    "ℹ️ CLUB_GROUP_ID=0 или нет club_group — kick пропущен uid=%s",
                    user_id,
                )

            await self.user_storage.mark_license_expired(user_id)
            card = await build_club_removal_card_html(
                self.user_storage,
                user_id,
                reason=REASON_BONUS_EXPIRED,
            )
            await self._notify_admin(card)
            await asyncio.sleep(0.5)

    async def _process_churn_outreach(self, today_msk: date):
        for block in self.CHURN_MESSAGES:
            days_after = block["days_after_exit"]
            anchor = today_msk - timedelta(days=days_after)
            slug = block["slug"]
            users = await self.user_storage.list_users_churn_exit_anchor_msk(anchor)

            for row in users:
                uid = row["user_id"]
                if await self.user_storage.get_user_active_license(uid):
                    continue
                first_name = row.get("first_name")
                claimed = await self.user_storage.try_claim_subscription_outreach(
                    uid, slug, today_msk
                )
                if not claimed:
                    continue

                if config.MEMBER_CHURN_AI_ENABLED and self._llm_client:
                    from bot.services.member_churn_outreach import (
                        generate_churn_outreach_html,
                    )

                    body = await generate_churn_outreach_html(
                        user_storage=self.user_storage,
                        llm_client=self._llm_client,
                        rag_stack=self.rag_stack,
                        user_id=uid,
                        first_name=first_name,
                        churn_block=block,
                    )
                else:
                    body = sub_txt.personalize_html(block["text"], first_name)

                kb = self._keyboard_reminder(block["keyboard"])
                ok = await self._send_html(uid, body, kb)
                if ok:
                    logger.info(
                        "📬 Churn slug=%s days_after_exit=%s user=%s anchor=%s",
                        slug,
                        days_after,
                        uid,
                        anchor,
                    )
                await asyncio.sleep(0.5)

    async def _send_html(
        self,
        user_id: int,
        text: str,
        keyboard: Optional[InlineKeyboardMarkup],
    ) -> bool:
        try:
            await self.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            return True
        except Exception as e:
            from bot.utils.telegram_errors import format_exception, is_user_unreachable_error

            if is_user_unreachable_error(e):
                logger.info(
                    "subscription_reminder skipped uid=%s: %s",
                    user_id,
                    format_exception(e),
                )
                await self.user_storage.deactivate_user(user_id)
                logger.info("🚫 User %s deactivated", user_id)
            else:
                logger.error(
                    "❌ send subscription_reminder uid=%s: %s",
                    user_id,
                    format_exception(e),
                )
            return False

    async def _on_rem_affiliate(self, query: CallbackQuery) -> None:
        if not self.referral_feature:
            await query.answer(sub_txt.rem_affiliate_unavailable_alert, show_alert=True)
            return
        await query.answer()
        user_id = query.from_user.id
        try:
            await self.referral_feature.show_affiliate_link(
                query.message, user_id, edit=True
            )
        except Exception as e:
            logger.error("rem_affiliate uid=%s: %s", user_id, e, exc_info=True)

    async def _on_churn_reply(self, query: CallbackQuery) -> None:
        user = query.from_user
        uid = user.id
        body = sub_txt.churn_reply_ticket_body
        topic = sub_txt.churn_reply_ticket_topic
        ticket_number = await self.user_storage.create_support_ticket(
            user_id=uid,
            topic=topic,
            message=body,
        )
        if not ticket_number:
            await query.answer(sub_txt.churn_reply_ticket_failed_alert, show_alert=True)
            return

        support = (
            self.feature_manager.get("support") if self.feature_manager else None
        )
        if support and hasattr(support, "send_admin_ticket_notification"):
            await support.send_admin_ticket_notification(
                ticket_number=ticket_number,
                user_id=uid,
                message_text=body,
                user=user,
                is_feedback=True,
            )
        else:
            logger.error("support feature недоступен — админы не уведомлены о %s", ticket_number)

        await query.answer(sub_txt.churn_reply_thanks_answer)
        if query.message:
            try:
                await render_user_screen(
                    query.message,
                    text=sub_txt.churn_reply_followup_html,
                    edit=True,
                )
            except Exception as e:
                logger.warning("churn_reply follow-up: %s", e)

    async def _on_churn18_feedback(self, query: CallbackQuery) -> None:
        raw = (query.data or "").replace(CHURN18_PREFIX, "", 1)
        if not raw:
            await query.answer(sub_txt.churn18_empty_callback_alert, show_alert=True)
            return

        label = CHURN18_LABEL_BY_KEY.get(raw)
        if not label:
            await query.answer(sub_txt.churn18_unknown_option_alert, show_alert=True)
            return

        user = query.from_user
        uid = user.id
        body = sub_txt.churn18_ticket_body(label=label)
        ticket_number = await self.user_storage.create_support_ticket(
            user_id=uid,
            topic=sub_txt.churn18_ticket_topic,
            message=body,
        )
        if not ticket_number:
            await query.answer(sub_txt.churn18_ticket_failed_alert, show_alert=True)
            return

        support = (
            self.feature_manager.get("support") if self.feature_manager else None
        )
        if support and hasattr(support, "send_admin_ticket_notification"):
            await support.send_admin_ticket_notification(
                ticket_number=ticket_number,
                user_id=uid,
                message_text=body,
                user=user,
                is_feedback=True,
            )
        else:
            logger.error("support feature недоступен — админы не уведомлены о %s", ticket_number)

        await query.answer(sub_txt.churn18_thanks_answer)
        if query.message:
            try:
                await render_user_screen(
                    query.message,
                    text=sub_txt.churn18_followup_html,
                    edit=True,
                )
            except Exception as e:
                logger.warning("churn18 follow-up: %s", e)

    async def _notify_admin(self, text: str) -> None:
        try:
            if not config.ADMIN_CHANNEL_ID:
                return
            thread_id = resolve_admin_service_thread_id()
            ok = await send_admin_html_message(
                self.bot, text, thread_id=thread_id
            )
            if not ok:
                logger.warning(
                    "subscription_reminder: админ-уведомление не доставлено "
                    "(thread_id=%s)",
                    thread_id,
                )
        except Exception as e:
            logger.error("❌ _notify_admin: %s", e)
