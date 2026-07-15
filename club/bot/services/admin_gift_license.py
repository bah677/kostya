"""Админ-команда /gift: выдача или продление лицензии без оплаты."""

from __future__ import annotations

import html as html_mod
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from aiogram.enums import ParseMode
from aiogram.types import User

from bot.utils.admin_channel import send_admin_html_message
from config import config, russian_days_phrase

logger = logging.getLogger(__name__)

_CLUB_NAME = "Любящие Бога"


def _format_admin_label(admin: User) -> str:
    if admin.username:
        return f"@{html_mod.escape(admin.username)} (<code>{admin.id}</code>)"
    name = " ".join(
        p for p in (admin.first_name or "", admin.last_name or "") if p
    ).strip()
    if name:
        return f"{html_mod.escape(name)} (<code>{admin.id}</code>)"
    return f"<code>{admin.id}</code>"


def _user_display_name(user_row: Optional[Dict[str, Any]]) -> str:
    if not user_row:
        return "Не указано"
    parts = [user_row.get("first_name") or "", user_row.get("last_name") or ""]
    name = " ".join(p for p in parts if p).strip()
    return name or "Не указано"


def _username_display(user_row: Optional[Dict[str, Any]]) -> str:
    un = (user_row or {}).get("username")
    return f"@{un}" if un else "нет username"


async def execute_admin_gift(
    *,
    user_storage,
    bot,
    feature_manager,
    message_copier=None,
    admin_user: User,
    target_user_id: int,
    days: int,
) -> str:
    """
    Выдаёт лицензию, уведомляет пользователя и топик оплат.
    Возвращает HTML-ответ для админа в чате команды.
    """
    if days < 1 or days > 3650:
        return "❌ Число дней должно быть от 1 до 3650."

    user_row = await user_storage.get_user(target_user_id)
    if not user_row:
        return (
            f"❌ Пользователь <code>{target_user_id}</code> не найден в базе. "
            "Нужен хотя бы один /start у этого аккаунта."
        )

    result = await user_storage.grant_admin_gift_license(
        target_user_id,
        days,
        admin_telegram_id=admin_user.id,
    )
    if not result:
        return "❌ Не удалось выдать лицензию (ошибка БД)."

    new_expiry: datetime = result["new_expires_at"]
    expires_str = new_expiry.strftime("%d.%m.%Y")
    days_phrase = russian_days_phrase(days)
    was_extension = bool(result.get("was_extension"))

    if was_extension:
        await _send_user_extension_message(
            bot,
            message_copier,
            target_user_id,
            days_phrase,
            expires_str,
        )
    else:
        club_group = feature_manager.get("club_group") if feature_manager else None
        if club_group:
            ok_invite = await club_group.send_admin_gift_invite(
                target_user_id, expires_str=expires_str
            )
            if not ok_invite:
                await _send_user_extension_message(
                    bot, message_copier, target_user_id, days_phrase, expires_str
                )
                logger.warning(
                    "admin gift: invite failed uid=%s, sent extension-style notice",
                    target_user_id,
                )
        else:
            await _send_user_extension_message(
                bot, message_copier, target_user_id, days_phrase, expires_str
            )

    await _notify_payment_topic(
        user_row=user_row,
        target_user_id=target_user_id,
        days=days,
        expires_str=expires_str,
        was_extension=was_extension,
        previous_expires_at=result.get("previous_expires_at"),
        admin_user=admin_user,
        bot=bot,
    )

    action = "продлена" if was_extension else "выдана"
    return (
        f"✅ Лицензия {action}: <code>{target_user_id}</code>, "
        f"+{days} дн., до <b>{expires_str}</b>."
    )


async def _send_user_extension_message(
    bot, message_copier, user_id: int, days_phrase: str, expires_str: str
) -> None:
    text = (
        "🎁 <b>Доступ в клуб продлён</b>\n\n"
        f"Мы добавили вам <b>{days_phrase}</b> доступа в закрытый клуб "
        f"<b>«{_CLUB_NAME}»</b> в подарок.\n\n"
        f"📆 Действует до: <b>{expires_str}</b>\n\n"
        "Узнать срок: /subs\n"
        "Если вы уже в группе — ничего делать не нужно. "
        "Ссылка при необходимости: /club"
    )
    try:
        sent = await bot.send_message(
            user_id, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
        if message_copier:
            row_id = await message_copier.save_outgoing(
                message=sent,
                source="admin_gift",
                subtype="extension",
            )
            if row_id is None:
                logger.warning(
                    "admin gift: extension not in messages uid=%s mid=%s",
                    user_id,
                    sent.message_id,
                )
    except Exception as e:
        logger.error("admin gift: user notify failed uid=%s: %s", user_id, e)


async def _notify_payment_topic(
    *,
    user_row: Dict[str, Any],
    target_user_id: int,
    days: int,
    expires_str: str,
    was_extension: bool,
    previous_expires_at: Optional[datetime],
    admin_user: User,
    bot,
) -> None:
    if not config.ADMIN_CHANNEL_ID:
        return
    thread_id = config.PAYMENT_THREAD_ID
    full_name = _user_display_name(user_row)
    username = _username_display(user_row)
    admin_label = _format_admin_label(admin_user)

    if was_extension:
        prev_str = (
            previous_expires_at.strftime("%d.%m.%Y")
            if previous_expires_at
            else "—"
        )
        title = "🎁 <b>Продление лицензии в подарок</b>"
        date_line = (
            f"📆 Было до: {prev_str} → стало до: {expires_str} "
            f"(+{days} дн.)"
        )
    else:
        title = "🎁 <b>Лицензия в подарок</b>"
        date_line = f"📆 +{days} дн., действует до: {expires_str}"

    text = (
        f"{title}\n\n"
        f"👤 <b>Пользователь:</b> {html_mod.escape(full_name)}\n"
        f"🆔 <b>User ID:</b> <code>{target_user_id}</code>\n"
        f"📱 <b>Username:</b> {html_mod.escape(username)}\n\n"
        f"{date_line}\n"
        f"👮 <b>Выдал:</b> {admin_label}"
    )
    ok = await send_admin_html_message(
        bot,
        text,
        thread_id=thread_id if thread_id and thread_id > 0 else None,
    )
    if not ok:
        logger.error("admin gift: payment topic notify failed uid=%s", target_user_id)
