"""Уведомления админ-канала и пользователей по доске желаний."""

from __future__ import annotations

import html
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import Message, User

from bot.services.wish_board_deeplink import build_wish_board_deeplink
from bot.texts import ru_wish_board as wb_txt
from bot.utils.admin_channel import (
    admin_channel_chat_id,
    edit_admin_channel_message,
    send_admin_html_message_main_bot,
)
from bot.utils.club_digest_topic import send_html_to_club_digest_topic
from bot.utils.telegram_errors import format_exception, is_topic_closed_error
from bot.utils.telegram_html import sanitize_telegram_html
from bot.utils.telegram_identity import resolve_telegram_bot_username
from config import config

logger = logging.getLogger(__name__)

CB_ADM_APPROVE = "wb:adm:ok:"
CB_ADM_REJECT = "wb:adm:no:"


def moderation_keyboard(wish_id: int):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=wb_txt.BTN_ADM_APPROVE,
                    callback_data=f"{CB_ADM_APPROVE}{wish_id}",
                ),
                InlineKeyboardButton(
                    text=wb_txt.BTN_ADM_REJECT,
                    callback_data=f"{CB_ADM_REJECT}{wish_id}",
                ),
            ]
        ]
    )


def _admin_display_name(user: Optional[User]) -> str:
    if not user:
        return "админ"
    name = (user.full_name or user.first_name or "").strip() or "админ"
    if user.username:
        return f"{name} (@{user.username})"
    return name


def append_moderation_resolution_block(
    base_html: str,
    *,
    title: str,
    admin: Optional[User] = None,
    extra: str = "",
) -> str:
    """Дописывает блок решения модератора к исходному тексту (как тикет ТП)."""
    base = (base_html or "").strip()
    while "\n\n\n" in base:
        base = base.replace("\n\n\n", "\n\n")
    ts = datetime.now().strftime("%d.%m.%Y %H:%M")
    block = (
        f"\n\n━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>{html.escape(title)}</b>\n"
        f"👤 <b>Модератор:</b> {html.escape(_admin_display_name(admin))}\n"
        f"⏰ <b>Время:</b> {ts}"
    )
    if extra:
        block += f"\n\n{sanitize_telegram_html(extra)}"
    return base + block


async def post_moderation_request(
    bot: Bot,
    *,
    wish: Dict[str, Any],
    requester: Optional[Dict[str, Any]],
    user_storage,
) -> Optional[int]:
    """Публикует заявку в админ-топик основным ботом; возвращает message_id."""
    topic_id = config.WISH_BOARD_ADMIN_TOPIC_ID
    if not topic_id:
        logger.warning("wish_board: admin topic not configured")
        return None

    text = wb_txt.format_wish_card(wish, requester=requester, for_admin=True)
    text = sanitize_telegram_html(
        f"{wb_txt.MODERATION_NEW_HEADER_HTML}\n\n{text}"
    )
    kb = moderation_keyboard(int(wish["id"]))

    msg_id = await send_admin_html_message_main_bot(
        bot,
        text,
        message_thread_id=topic_id,
        reply_markup=kb,
    )
    if msg_id is None:
        logger.error("wish_board: moderation post failed (main bot only)")
    return msg_id


async def post_admin_lifecycle(
    bot: Bot,
    *,
    event: str,
    wish: Dict[str, Any],
    extra: str = "",
) -> bool:
    topic_id = config.WISH_BOARD_ADMIN_TOPIC_ID
    if not topic_id:
        return False
    text = wb_txt.admin_event_html(event, wish, extra=extra)
    return (
        await send_admin_html_message_main_bot(
            bot, text, message_thread_id=topic_id
        )
        is not None
    )


async def notify_user_html(
    bot: Bot,
    user_id: int,
    text: str,
    *,
    reply_markup=None,
) -> bool:
    try:
        await bot.send_message(
            chat_id=user_id,
            text=sanitize_telegram_html(text),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
        return True
    except Exception as e:
        logger.warning("wish_board notify uid=%s failed: %s", user_id, e)
        return False


def rating_prompt_markup(wish_id: int):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    from bot.utils.user_ui import with_main_menu

    stars = [
        InlineKeyboardButton(
            text=f"⭐{i}",
            callback_data=f"wb:rate:{wish_id}:{i}",
        )
        for i in range(1, 6)
    ]
    return with_main_menu(
        [stars, [InlineKeyboardButton(text=wb_txt.BTN_BACK_HUB, callback_data="wb:hub")]]
    )


async def notify_rating_prompt(bot: Bot, user_id: int, wish_id: int) -> bool:
    return await notify_user_html(
        bot,
        user_id,
        wb_txt.COMPLETED_HTML,
        reply_markup=rating_prompt_markup(wish_id),
    )


def wish_is_digest_postable(wish: Dict[str, Any]) -> bool:
    """Можно ли публиковать просьбу в топике клуба (только открытая, без дубля)."""
    return (
        (wish.get("status") or "") == "open"
        and not wish.get("digest_notice_message_id")
    )


async def post_digest_wish(
    bot: Bot,
    user_storage,
    wish: Dict[str, Any],
) -> bool:
    """Одна просьба — отдельное сообщение в топике (для ответа при исполнении)."""
    topic_id = config.WISH_BOARD_DIGEST_TOPIC_ID
    group_id = config.CLUB_GROUP_ID
    if not topic_id or not group_id:
        return False

    if not wish_is_digest_postable(wish):
        logger.debug(
            "wish_board digest skip id=%s status=%s digest_msg=%s",
            wish.get("id"),
            wish.get("status"),
            wish.get("digest_notice_message_id"),
        )
        return False

    bot_username = await resolve_telegram_bot_username(bot)
    respond_url = (
        build_wish_board_deeplink(bot_username, wish_id=int(wish["id"]))
        if bot_username
        else ""
    )
    html_text = wb_txt.digest_single_post_html(wish, respond_url=respond_url)
    msg_id = await send_html_to_club_digest_topic(
        bot,
        chat_id=int(group_id),
        topic_id=int(topic_id),
        html=html_text,
        log_prefix="wish_board_digest",
        return_message_id=True,
    )
    if not msg_id:
        return False
    await user_storage.wish_set_digest_notice_message_id(int(wish["id"]), int(msg_id))
    return True


async def post_group_reminder_wish(
    bot: Bot,
    user_storage,
    wish: Dict[str, Any],
) -> bool:
    """Повторный пост в группу: просьба долго открыта, ждёт ангела."""
    topic_id = config.WISH_BOARD_DIGEST_TOPIC_ID
    group_id = config.CLUB_GROUP_ID
    if not topic_id or not group_id:
        return False
    if (wish.get("status") or "") != "open":
        return False

    bot_username = await resolve_telegram_bot_username(bot)
    respond_url = (
        build_wish_board_deeplink(bot_username, wish_id=int(wish["id"]))
        if bot_username
        else ""
    )
    html_text = wb_txt.group_reminder_post_html(wish, respond_url=respond_url)
    reply_to = wish.get("digest_notice_message_id")
    sent = await send_html_to_club_digest_topic(
        bot,
        chat_id=int(group_id),
        topic_id=int(topic_id),
        html=html_text,
        log_prefix="wish_board_group_reminder",
        reply_to_message_id=int(reply_to) if reply_to else None,
    )
    if not sent:
        return False
    return await user_storage.wish_record_group_reminder(int(wish["id"]))


async def post_digest_items(
    bot: Bot, user_storage, wishes: list[Dict[str, Any]]
) -> bool:
    if not wishes:
        return False
    ok = False
    for wish in wishes:
        if await post_digest_wish(bot, user_storage, wish):
            ok = True
    return ok


async def post_angel_pool_donation(
    bot: Bot,
    *,
    amount: str,
    currency_label: str,
    count: int,
) -> bool:
    """Пост в топик доски добрых дел об ангельском взносе."""
    from bot.texts import ru_angel_pool as ap_txt

    topic_id = config.WISH_BOARD_DIGEST_TOPIC_ID
    group_id = config.CLUB_GROUP_ID
    if not topic_id or not group_id or count <= 0:
        return False

    text = sanitize_telegram_html(
        ap_txt.GROUP_TOPIC_HTML.format(
            amount=amount,
            currency_label=currency_label,
            count=count,
            count_word=ap_txt.count_word(count),
        )
    )
    return bool(
        await send_html_to_club_digest_topic(
            bot,
            chat_id=int(group_id),
            topic_id=int(topic_id),
            html=text,
            log_prefix="angel_pool_digest",
        )
    )


async def reply_group_wish_fulfilled(bot: Bot, wish: Dict[str, Any]) -> bool:
    """Ответ в клубном топике на исходный пост просьбы."""
    topic_id = config.WISH_BOARD_DIGEST_TOPIC_ID
    group_id = config.CLUB_GROUP_ID
    reply_to = wish.get("digest_notice_message_id")
    if not topic_id or not group_id or not reply_to:
        return False

    wid = int(wish["id"])
    text = sanitize_telegram_html(wb_txt.group_fulfilled_reply_html(wid))
    chat_id = int(group_id)
    thread_id = int(topic_id)
    reply_id = int(reply_to)

    reopened = False
    try:
        try:
            await bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                reply_to_message_id=reply_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return True
        except Exception as e:
            if not is_topic_closed_error(e):
                logger.error(
                    "wish_board fulfilled reply wish=%s: %s", wid, format_exception(e)
                )
                return False
            await bot.reopen_forum_topic(chat_id=chat_id, message_thread_id=thread_id)
            reopened = True
            await bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                reply_to_message_id=reply_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return True
    except Exception as e:
        logger.error(
            "wish_board fulfilled reply wish=%s: %s", wid, format_exception(e)
        )
        return False
    finally:
        if reopened:
            try:
                await bot.close_forum_topic(chat_id=chat_id, message_thread_id=thread_id)
            except Exception as e:
                logger.warning(
                    "wish_board fulfilled reply close topic: %s", format_exception(e)
                )


async def clear_moderation_buttons(bot: Bot, message: Optional[Message]) -> None:
    if not message:
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reply_markup=None,
        )
    except Exception as e:
        logger.debug("wish_board clear moderation buttons: %s", e)


async def edit_moderation_resolved(
    bot: Bot,
    *,
    wish: Dict[str, Any],
    resolved_label: str,
    requester: Optional[Dict[str, Any]] = None,
    original_message: Optional[Message] = None,
    admin: Optional[User] = None,
    extra: str = "",
) -> None:
    """Обновляет исходное сообщение модерации — дописывает блок решения, убирает кнопки."""
    if original_message and (original_message.text or original_message.caption):
        base = original_message.text or original_message.caption or ""
        chat_id = original_message.chat.id
        message_id = int(original_message.message_id)
    else:
        msg_id = wish.get("admin_notice_message_id")
        cid = admin_channel_chat_id()
        if not msg_id or not cid:
            return
        base = sanitize_telegram_html(
            f"{wb_txt.MODERATION_NEW_HEADER_HTML}\n\n"
            + wb_txt.format_wish_card(wish, requester=requester, for_admin=True)
        )
        chat_id = cid
        message_id = int(msg_id)

    new_text = append_moderation_resolution_block(
        base,
        title=resolved_label.replace("✅ ", "").replace("❌ ", ""),
        admin=admin,
        extra=extra,
    )
    await edit_admin_channel_message(
        bot,
        chat_id=chat_id,
        message_id=message_id,
        text=new_text,
        reply_markup=None,
    )
