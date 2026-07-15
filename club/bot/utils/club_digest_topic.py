"""Отправка HTML в топик дайджеста клуба (в т.ч. если топик закрыт)."""

from __future__ import annotations

import logging
from typing import Optional

from aiogram import Bot
from aiogram.enums import ParseMode

from bot.utils.telegram_errors import format_exception, is_topic_closed_error
from bot.utils.telegram_send import send_telegram_html_chunks

logger = logging.getLogger(__name__)


async def send_html_to_club_digest_topic(
    bot: Bot,
    *,
    chat_id: int,
    topic_id: int,
    html: str,
    log_prefix: str = "club_topic",
    return_message_id: bool = False,
    reply_to_message_id: Optional[int] = None,
) -> bool | Optional[int]:
    """Публикует HTML в форум-топик; при TOPIC_CLOSED — reopen → send → close."""
    if not chat_id or not topic_id or not (html or "").strip():
        return None if return_message_id else False

    reopened = False
    first_message_id: Optional[int] = None
    try:
        try:
            first_message_id = await _send_chunks(
                bot,
                chat_id=chat_id,
                topic_id=topic_id,
                html=html,
                return_first_message_id=return_message_id,
                reply_to_message_id=reply_to_message_id,
            )
            return first_message_id if return_message_id else True
        except Exception as e:
            if not is_topic_closed_error(e):
                logger.error("[%s] send topic %s: %s", log_prefix, topic_id, e)
                return None if return_message_id else False
            logger.info(
                "[%s] топик %s закрыт (TOPIC_CLOSED), reopen → send → close",
                log_prefix,
                topic_id,
            )
            await bot.reopen_forum_topic(chat_id=chat_id, message_thread_id=topic_id)
            reopened = True
            first_message_id = await _send_chunks(
                bot,
                chat_id=chat_id,
                topic_id=topic_id,
                html=html,
                return_first_message_id=return_message_id,
                reply_to_message_id=reply_to_message_id,
            )
            return first_message_id if return_message_id else True
    except Exception as e:
        logger.error("[%s] send topic %s: %s", log_prefix, topic_id, format_exception(e))
        return None if return_message_id else False
    finally:
        if reopened:
            try:
                await bot.close_forum_topic(chat_id=chat_id, message_thread_id=topic_id)
            except Exception as e:
                logger.warning(
                    "[%s] не удалось закрыть топик %s: %s",
                    log_prefix,
                    topic_id,
                    format_exception(e),
                )


async def _send_chunks(
    bot: Bot,
    *,
    chat_id: int,
    topic_id: int,
    html: str,
    return_first_message_id: bool = False,
    reply_to_message_id: Optional[int] = None,
) -> Optional[int]:
    return await send_telegram_html_chunks(
        bot,
        chat_id,
        html,
        message_thread_id=topic_id,
        reply_to_message_id=reply_to_message_id,
        return_first_message_id=return_first_message_id,
        sanitize=False,
    )
