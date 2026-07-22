"""Уведомление админ-канала о успешном донате без заказа (order_id IS NULL)."""

from __future__ import annotations

import html as html_module
import json
import logging
from typing import Any, Dict, Optional

from aiogram import Bot

from bot.utils.admin_channel import send_admin_html_message
from bot.services.donation_marathon_progress import marathon_admin_notify_block
from config import config

logger = logging.getLogger(__name__)

_CLUB_PROMO_AFTER_DONATION_URL = (
    "https://t.me/Talk_God_Bot?start=promo_89dd9a5203454d9a9355fafa0af69ff0"
)
_CLUB_PROMO_AFTER_DONATION_TEXT = (
    "Спасибо, что поддерживаешь проект 🙏\n\n"
    "Твой добрый поступок не остался без ответа. Мы решили подарить тебе персональную "
    "скидку на вход в клуб «Любящие Бога».\n\n"
    "Обычная цена: 1 200 ₽/мес\n"
    "Цена для тебя: 840 ₽/мес (скидка 30%)\n\n"
    "Это живое пространство, где мы вместе ищем Бога, разбираем деньги, отношения, "
    "призвание — не в лекциях, а в диалоге и молитве.\n\n"
    "Хочешь узнать, что внутри, и задать любые вопросы?\n\n"
    "👇 Нажми на кнопку ниже и вступай в нашу семью."
)


def _telegram_block_from_payment(payment: Dict[str, Any]) -> tuple[str, str, str]:
    user_data = payment.get("user_telegram_data") or {}
    if isinstance(user_data, str):
        try:
            user_data = json.loads(user_data)
        except json.JSONDecodeError:
            user_data = {}
    if not isinstance(user_data, dict):
        user_data = {}
    full_name = f"{user_data.get('first_name', '')} {user_data.get('last_name', '')}".strip()
    full_name = full_name or "Не указано"
    username_display = (
        "@" + user_data["username"] if user_data.get("username") else "нет username"
    )
    return full_name, username_display, str(payment.get("user_id") or "")


def _fmt_rub_line(rub_amount: Optional[float]) -> str:
    if rub_amount is None:
        return "💵 <b>В рублях:</b> —"
    v = float(rub_amount)
    if abs(v - round(v)) < 0.005:
        rub_s = str(int(round(v)))
    else:
        rub_s = f"{v:.2f}"
    return f"💵 <b>В рублях:</b> {html_module.escape(rub_s)} RUB"


async def notify_admins_standalone_donation_success(
    bot: Bot,
    user_storage,
    payment: Dict[str, Any],
    *,
    rub_amount: Optional[float] = None,
    kind: str = "donation",
) -> None:
    if not config.ADMIN_CHANNEL_ID:
        logger.warning("⚠️ ADMIN_CHANNEL_ID not configured; skip standalone donation notify")
        return

    user_id = int(payment["user_id"])
    full_name, username_display, _uid_str = _telegram_block_from_payment(payment)

    amt = payment.get("amount")
    currency = (payment.get("currency") or "").strip().upper() or ""

    resolved_rub = rub_amount
    if resolved_rub is None and payment.get("amount_rub") is not None:
        resolved_rub = float(payment["amount_rub"])
    if resolved_rub is None and currency == "RUB" and amt is not None:
        resolved_rub = float(amt)

    source_name: Optional[str] = None
    try:
        source_name = await user_storage.get_last_referral_source(user_id)
    except Exception as e:
        logger.debug("referral source for notify: %s", e)

    assistant_n = await user_storage.get_assistant_messages_count(user_id)

    amount_bit = html_module.escape(str(amt)).strip()
    curr_bit = html_module.escape(currency)

    kind_labels = {
        "donation": "💰 ДОНАТ",
        "subscription_initial": "💰 ЕЖЕМЕСЯЧНАЯ ПОДДЕРЖКА (первое списание)",
        "subscription_renewal": "💰 ЕЖЕМЕСЯЧНАЯ ПОДДЕРЖКА (продление)",
    }
    header = kind_labels.get(kind, kind_labels["donation"])

    notification_text = (
        f"{header}\n\n"
        f"💰 <b>Сумма:</b> {amount_bit} {curr_bit}\n"
        f"{_fmt_rub_line(resolved_rub)}\n"
        f"👤 <b>Пользователь:</b> {html_module.escape(full_name)}\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
        f"📱 <b>Username:</b> {html_module.escape(username_display)}\n"
        f"🔗 <b>Источник:</b> {html_module.escape(source_name or 'неизвестно')}\n"
        f"💬 <b>Ответов ассистента в истории:</b> "
        f"{html_module.escape(str(assistant_n or '0'))}\n"
    )
    marathon_block = await marathon_admin_notify_block(user_storage)
    if marathon_block:
        notification_text += marathon_block

    admin_thread_id = getattr(config, "PAYMENT_THREAD_ID", None) or 0
    ok = await send_admin_html_message(
        bot,
        notification_text,
        thread_id=admin_thread_id if admin_thread_id and admin_thread_id > 0 else None,
    )
    if not ok:
        logger.error("❌ Failed standalone donation admin notify")


async def send_donation_club_promo_message(bot, user_id: int) -> None:
    """Второе сообщение после успешного доната: промо клуба со скидкой."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    try:
        btn = InlineKeyboardButton(
            text="Клуб «Любящие Бога»",
            url=_CLUB_PROMO_AFTER_DONATION_URL,
        )
        btn.style = "success"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[btn]])
        await bot.send_message(
            user_id,
            _CLUB_PROMO_AFTER_DONATION_TEXT,
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.warning(
            "Не удалось отправить промо клуба после доната user=%s: %s",
            user_id,
            e,
        )
