"""
Фича реферальной программы.
Позволяет пользователям получать свою реферальную ссылку.
Регистрирует рефералов только для новых пользователей и только один раз.
"""

import html
import logging
from datetime import datetime
from typing import Any, Dict

from aiogram import Dispatcher
from aiogram.enums import ParseMode
from aiogram.types import Message

from bot.features.base import BaseFeature
from bot.texts import ru_referral as ref_txt
from bot.utils.telegram_identity import resolve_telegram_bot_username
from bot.utils.user_ui import render_user_screen

logger = logging.getLogger(__name__)


def _invite_display_name(row: Dict[str, Any]) -> str:
    fn = (row.get("first_name") or "").strip()
    ln = (row.get("last_name") or "").strip()
    un = (row.get("username") or "").strip()
    full = (fn + " " + ln).strip()
    if full:
        return full
    if un:
        return f"@{un}"
    return ref_txt.DEFAULT_INVITE_DISPLAY_NAME


def _format_invited_line(row: Dict[str, Any]) -> str:
    name = html.escape(_invite_display_name(row))
    has_paid = bool(row.get("has_paid"))
    bonus_done = bool(row.get("referral_bonus_unlocked") or row.get("bonus_granted"))
    if has_paid:
        tail = (
            ref_txt.REFERRAL_PAID_BONUS_DONE
            if bonus_done
            else ref_txt.REFERRAL_PAID_BONUS_PENDING
        )
    else:
        tail = ref_txt.REFERRAL_NOT_PAID
    rd = row.get("referral_date")
    dt = ""
    if isinstance(rd, datetime):
        dt = rd.strftime("%d.%m.%Y")
    elif rd:
        dt = str(rd)[:10]
    tail_esc = html.escape(tail)
    if dt:
        return ref_txt.invited_line_with_date(
            name_esc=name,
            date_esc=html.escape(dt),
            tail_esc=tail_esc,
        )
    return ref_txt.invited_line_no_date(name_esc=name, tail_esc=tail_esc)


class ReferralFeature(BaseFeature):
    """Фича реферальной программы"""

    @property
    def name(self) -> str:
        return "referral"

    async def initialize(self) -> None:
        """Инициализация фичи."""
        logger.info(f"[{self.name}] Фича инициализирована")

    def __init__(self, user_storage, bot):
        super().__init__()
        self.user_storage = user_storage
        self.aiogram_bot = bot

    def register_handlers(self, dp: Dispatcher) -> None:
        """Регистрирует обработчики фичи."""
        # Команда /affiliate обрабатывается в commands.py

    async def show_affiliate_link(
        self, message: Message, user_id: int, *, edit: bool = False
    ) -> None:
        """Реферальная ссылка и краткая статистика успехов пользователя."""
        try:
            bot_username = await resolve_telegram_bot_username(self.aiogram_bot)
            if not bot_username:
                await render_user_screen(
                    message,
                    text=ref_txt.AFFILIATE_BOT_USERNAME_ERROR_HTML,
                    edit=edit,
                )
                return

            referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
            stats = await self.user_storage.get_referral_stats(user_id)
            invites = stats.get("total", 0)
            monthly = stats.get("monthly", 0)
            paid = stats.get("paid", 0)
            bonuses = stats.get("bonuses_given", 0)

            link_esc = html.escape(referral_link)
            lines = [
                ref_txt.affiliate_header_html(
                    referral_link_esc=link_esc,
                    referral_link_href=html.escape(referral_link, quote=True),
                ),
                ref_txt.affiliate_stats_block_html(
                    invites=invites,
                    monthly=monthly,
                    paid=paid,
                    bonuses=bonuses,
                ),
            ]

            recent_rows = await self.user_storage.get_referrals_list(user_id, limit=5)
            if recent_rows:
                lines.append(ref_txt.AFFILIATE_RECENT_INVITES_HEADER)
                lines.extend(_format_invited_line(r) + "\n" for r in recent_rows)
                lines.append("\n")
            elif invites == 0:
                lines.append(ref_txt.AFFILIATE_NO_INVITES_YET)

            lines.append(ref_txt.AFFILIATE_THANKS_FOOTER)

            await render_user_screen(
                message,
                text="".join(lines),
                edit=edit,
                disable_web_page_preview=True,
            )
            logger.info(f"✅ Affiliate link shown to user_id={user_id}")

        except Exception as e:
            logger.error(f"❌ Error showing affiliate link for user_id={user_id}: {e}", exc_info=True)
            await render_user_screen(
                message, text=ref_txt.AFFILIATE_ERROR_HTML, edit=edit
            )

    async def register_referral(self, message: Message, referrer_id_str: str, is_new_user: bool) -> None:
        """
        Регистрирует реферала, только если пользователь новый.

        Args:
            message: сообщение от пользователя
            referrer_id_str: ID реферера в виде строки
            is_new_user: флаг, был ли пользователь в БД до этого
        """
        try:
            user_id = message.from_user.id
            try:
                referrer_id = int(referrer_id_str)
            except ValueError:
                campaign_name = await self.user_storage.get_ref_key_name(
                    referrer_id_str
                )
                if campaign_name is not None:
                    logger.info(
                        "Named ref link (campaign, no user referrer): ref_key=%s "
                        "name=%s new_user=%s user_id=%s",
                        referrer_id_str,
                        campaign_name,
                        is_new_user,
                        user_id,
                    )
                else:
                    logger.warning(
                        "Invalid ref payload (not int, not in ref_keys): "
                        "ref_key=%s user_id=%s",
                        referrer_id_str,
                        user_id,
                    )
                return

            if not is_new_user:
                logger.info(f"User {user_id} already existed, skipping referral")
                return

            if referrer_id == user_id:
                logger.info(f"Self-referral attempt by {user_id}")
                return

            referrer = await self.user_storage.get_user(referrer_id)
            if not referrer:
                logger.warning(f"Referrer {referrer_id} not found")
                return

            created = await self.user_storage.process_referral(user_id, referrer_id)
            if created:
                logger.info(f"✅ Referral registered: {user_id} -> {referrer_id}")
                await self._notify_referrer(referrer_id, message.from_user.first_name)
            else:
                logger.info(f"Referral not created (already linked or conflict): {user_id}")

        except Exception as e:
            logger.error(f"❌ Error registering referral: {e}", exc_info=True)

    async def _notify_referrer(self, referrer_id: int, user_name: str) -> None:
        """Отправляет уведомление рефереру о новом реферале."""
        try:
            user_display = user_name or ref_txt.REFERRER_NOTIFY_DEFAULT_NAME

            message_text = ref_txt.referrer_notify_html(
                user_display_esc=html.escape(user_display)
            )

            await self.aiogram_bot.send_message(
                referrer_id,
                message_text,
                parse_mode=ParseMode.HTML,
            )
            logger.info(f"✅ Referral notification sent to {referrer_id}")

        except Exception as e:
            logger.error(f"❌ Failed to send referral notification to {referrer_id}: {e}")
