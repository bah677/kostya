"""
Реферальная программа: приглашения по ссылке без бонусов за оплату (в отличие от клубного сценария).
"""

import html
import logging
from datetime import datetime
from typing import Any, Dict

from aiogram.enums import ParseMode
from aiogram.types import Message

from bot.features.referral import ReferralFeature
from bot.utils.telegram_identity import resolve_telegram_bot_username

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
    return "Участник"


def parse_referrer_id_arg(raw: str) -> int | None:
    """Парсит user_id из аргумента: ``304631563`` или ``ref_304631563``."""
    s = (raw or "").strip()
    if s.lower().startswith("ref_"):
        s = s[4:].strip()
    try:
        val = int(s)
        return val if val > 0 else None
    except ValueError:
        return None


def _invited_line(row: Dict[str, Any]) -> str:
    name = html.escape(_invite_display_name(row))
    rd = row.get("referral_date")
    dt = ""
    if isinstance(rd, datetime):
        dt = rd.strftime("%d.%m.%Y")
    elif rd:
        dt = str(rd)[:10]
    if dt:
        return f"• {name} ({html.escape(dt)})\n"
    return f"• {name}\n"


class ReferralProgramFeature(ReferralFeature):
    """Тексты /affiliate и уведомления рефереру (name фичи — ``referral``, как у базового класса)."""

    async def register_referral(
        self, message: Message, referrer_id_str: str, is_new_user: bool
    ) -> None:
        """
        Реферал — пользователь, запустивший бота по ссылке ``/start ref_<id>``.
        Не требуется оплата; не требуется, чтобы он впервые появился в БД на этом /start.
        """
        del is_new_user  # в Библии не используем — важен факт перехода по ссылке
        try:
            user_id = message.from_user.id
            referrer_id = int(referrer_id_str)

            if referrer_id == user_id:
                logger.info("Self-referral attempt by %s", user_id)
                return

            referrer = await self.user_storage.get_user(referrer_id)
            if not referrer:
                logger.warning("Referrer %s not found", referrer_id)
                return

            created = await self.user_storage.process_referral(user_id, referrer_id)
            if created:
                logger.info("✅ Referral registered: %s -> %s", user_id, referrer_id)
                name = message.from_user.first_name if message.from_user else ""
                await self._notify_referrer(referrer_id, name or "")
            else:
                logger.info("Referral not created (already linked or duplicate): %s", user_id)

        except ValueError as e:
            logger.error("❌ Invalid referrer_id: %s, error: %s", referrer_id_str, e)
        except Exception as e:
            logger.error("❌ Error registering referral: %s", e, exc_info=True)

    async def show_affiliate_link(self, message: Message, user_id: int) -> None:
        try:
            bot_username = await resolve_telegram_bot_username(self.aiogram_bot)
            if not bot_username:
                await message.answer(
                    "❌ Не удалось узнать адрес бота для ссылки. "
                    "Задайте в .env <code>TELEGRAM_BOT_USERNAME</code> (username без @).",
                    parse_mode=ParseMode.HTML,
                )
                return

            referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
            stats = await self.user_storage.get_referral_stats(user_id)
            invites = stats.get("total", 0)
            monthly = stats.get("monthly", 0)

            lines = [
                "<b>🤝 Поделись ссылкой с друзьями</b>\n\n"
                f'<a href="{html.escape(referral_link, quote=True)}">{html.escape(referral_link)}</a>\n\n'
                "<b>Статистика</b>\n"
                f"• Переходов по ссылке (всего): <b>{invites}</b>\n"
                f"• Новых за последние 30 дней: <b>{monthly}</b>\n\n",
            ]

            recent_rows = await self.user_storage.get_referrals_list(user_id, limit=5)
            if recent_rows:
                lines.append("<b>Последние приглашённые</b> (до 5):\n")
                lines.extend(_invited_line(r) for r in recent_rows)
                lines.append("\n")
            elif invites == 0:
                lines.append(
                    "<i>Пока никто не зашёл по ссылке — отправьте её тем, кому будет полезно.</i>\n\n"
                )

            lines.append("Спасибо, что делитесь! 🙏")

            await message.answer(
                "".join(lines),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            logger.info("✅ Affiliate link user_id=%s", user_id)

        except Exception as e:
            logger.error("❌ Affiliate: %s", e, exc_info=True)
            await message.answer(
                "❌ Произошла ошибка при формировании ссылки. Попробуйте позже."
            )

    async def show_referral_stats(
        self,
        message: Message,
        user_id: int,
        *,
        viewer_id: int | None = None,
    ) -> None:
        """Статистика переходов по персональной реферальной ссылке."""
        try:
            bot_username = await resolve_telegram_bot_username(self.aiogram_bot)
            stats = await self.user_storage.get_referral_stats(user_id)
            total = int(stats.get("total") or 0)
            weekly = int(stats.get("weekly") or 0)
            monthly = int(stats.get("monthly") or 0)
            paid = int(stats.get("paid") or 0)

            own_view = viewer_id is None or viewer_id == user_id
            if own_view:
                title = "<b>📊 Статистика по вашей ссылке</b>\n\n"
                link_label = "Ваша ссылка"
            else:
                title = (
                    f"<b>📊 Статистика реферальной ссылки</b>\n"
                    f"<code>user_id={user_id}</code>\n\n"
                )
                link_label = "Ссылка пользователя"

            lines = [
                title,
                f"• Всего пришло: <b>{total}</b>\n",
                f"• За 7 дней: <b>{weekly}</b>\n",
                f"• За 30 дней: <b>{monthly}</b>\n",
                f"• С донатом: <b>{paid}</b>\n\n",
            ]

            if bot_username:
                referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
                lines.append(
                    f"<b>{link_label}:</b>\n"
                    f'<a href="{html.escape(referral_link, quote=True)}">{html.escape(referral_link)}</a>\n\n'
                )

            recent_rows = await self.user_storage.get_referrals_list(user_id, limit=10)
            if recent_rows:
                lines.append("<b>Последние приглашённые</b> (до 10):\n")
                for row in recent_rows:
                    name = html.escape(_invite_display_name(row))
                    rd = row.get("referral_date")
                    dt = rd.strftime("%d.%m.%Y") if isinstance(rd, datetime) else ""
                    paid_mark = " 💳" if row.get("has_paid") else ""
                    if dt:
                        lines.append(f"• {name} ({html.escape(dt)}){paid_mark}\n")
                    else:
                        lines.append(f"• {name}{paid_mark}\n")
            elif total == 0:
                lines.append(
                    "<i>Пока никто не зашёл по ссылке. Отправьте её друзьям — "
                    "статистика появится здесь.</i>\n"
                )

            await message.answer(
                "".join(lines),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            logger.info("✅ Referral stats user_id=%s total=%s", user_id, total)

        except Exception as e:
            logger.error("❌ Referral stats: %s", e, exc_info=True)
            await message.answer(
                "❌ Не удалось загрузить статистику. Попробуйте позже."
            )

    async def _notify_referrer(self, referrer_id: int, user_name: str) -> None:
        if referrer_id == 367302291:
            logger.info(
                "🚫 Referral notify suppressed (hardcoded skip) for referrer_id=%s",
                referrer_id,
            )
            return
        try:
            user_display = user_name or "Новый пользователь"
            message_text = (
                "<b>✨ Твоя ссылка стала мостом к Свету.</b>\n\n"
                f"<b>{html.escape(user_display)}</b> только что зашёл в бота по твоей рекомендации.\n\n"
                "И, возможно, именно сегодня он получил то слово, которое поддержало, исцелило, "
                "дало направление или просто согрело сердце.\n\n"
                "📖\n<blockquote>Блаженны миротворцы, ибо они будут наречены сынами Божиими\n\n"
                "<i>(Мф. 5:9)</i></blockquote>"
            )
            await self.aiogram_bot.send_message(
                referrer_id,
                message_text,
                parse_mode=ParseMode.HTML,
            )
            logger.info("✅ Referral notify sent to %s", referrer_id)
        except Exception as e:
            logger.error("❌ Referral notify %s: %s", referrer_id, e)
