"""Приветствие нового участника в закрытой группе клуба (ветка welcome в форуме)."""

from __future__ import annotations

import html as html_mod
import logging
import time
from typing import TYPE_CHECKING, Dict, Tuple

from aiogram.enums import ParseMode

from bot.logging.club_join_debug import log_event
from bot.texts import ru_club_welcome as welcome_txt

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import User

logger = logging.getLogger(__name__)

_dedup_ts: Dict[Tuple[int, int], float] = {}
_DEDUP_TTL_SEC = 120.0


def _dedup_should_skip(chat_id: int, user_id: int) -> bool:
    now = time.monotonic()
    cutoff = now - _DEDUP_TTL_SEC
    for k, ts in list(_dedup_ts.items()):
        if ts < cutoff:
            del _dedup_ts[k]
    key = (chat_id, user_id)
    if key in _dedup_ts:
        return True
    _dedup_ts[key] = now
    return False


async def send_club_member_welcome(
    bot: "Bot",
    chat_id: int,
    user: "User",
    *,
    welcome_topic_id: int,
    is_forum: bool,
    telegram_message_id_in: int | None = None,
) -> None:
    """
    Отправляет приветствие в чат (в форуме — в ветку welcome_topic_id).
    Дедуп: два события подряд (message + chat_member) не дублируют текст.
    """
    if user.is_bot:
        return

    if is_forum and not welcome_topic_id:
        log_event(
            "welcome_skipped",
            reason="forum_without_topic_id",
            chat_id=chat_id,
            user_id=user.id,
        )
        logger.error(
            "Группа %s форум, но WELCOME_TOPIC_ID=0: задайте id ветки приветствия в .env",
            chat_id,
        )
        return

    if _dedup_should_skip(chat_id, user.id):
        log_event(
            "welcome_skipped",
            reason="dedup_chat_member_and_service_message",
            chat_id=chat_id,
            user_id=user.id,
        )
        return

    if user.username:
        un = user.username
        mention = f'<a href="https://t.me/{html_mod.escape(un)}">@{html_mod.escape(un)}</a>'
    else:
        name = html_mod.escape(user.full_name or f"Пользователь {user.id}")
        mention = f'<a href="tg://user?id={user.id}">{name}</a>'

    welcome_text = (
        f"{welcome_txt.WELCOME_BODY_PREFIX}"
        f"{mention}{welcome_txt.WELCOME_BODY_SUFFIX}"
    )

    kwargs: dict = {
        "chat_id": chat_id,
        "text": welcome_text,
        "parse_mode": ParseMode.HTML,
    }
    if welcome_topic_id:
        kwargs["message_thread_id"] = welcome_topic_id

    await bot.send_message(**kwargs)
    logger.info(
        "Welcome sent to user %s in chat %s thread=%s",
        user.id,
        chat_id,
        welcome_topic_id or "(none)",
    )
    log_event(
        "welcome_sent",
        user_id=user.id,
        chat_id=chat_id,
        message_thread_id=welcome_topic_id or None,
        telegram_message_id_in=telegram_message_id_in,
    )
