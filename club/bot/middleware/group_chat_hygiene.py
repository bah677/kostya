"""
Опциональная фича для «живого» клубного чата: приветствие при вступлении,
удаление служебных сообщений.

Подключайте middleware только когда в проекте есть группа (``CLUB_GROUP_ID``).
DM-only или бот без такого чата не регистрируют этот слой — логики нет на пути апдейтов.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Set

from aiogram import BaseMiddleware, Bot
from aiogram.types import Message, Update

from bot.logging.club_join_debug import log_event
from bot.utils.club_welcome import send_club_member_welcome
from config import config

logger = logging.getLogger(__name__)


class GroupChatHygieneMiddleware(BaseMiddleware):
    SERVICE_MESSAGE_FIELDS: Set[str] = {
        "new_chat_members",
        "left_chat_member",
        "group_chat_created",
        "supergroup_chat_created",
        "channel_chat_created",
        "migrate_to_chat_id",
        "migrate_from_chat_id",
        "pinned_message",
        "forum_topic_created",
        "forum_topic_closed",
        "forum_topic_reopened",
        "forum_topic_edited",
        "forum_topic_pinned",
        "forum_topic_unpinned",
        "general_forum_topic_hidden",
        "general_forum_topic_unhidden",
        "write_allowed",
        "message_auto_delete_timer_changed",
    }

    def __init__(self, bot: Bot, club_group_id: int, welcome_topic_id: int):
        super().__init__()
        self.bot = bot
        self.club_group_id = club_group_id
        self.welcome_topic_id = welcome_topic_id
        logger.info(
            "GroupChatHygiene включён: club_group_id=%s welcome_topic_id=%s",
            club_group_id,
            welcome_topic_id,
        )

    def _is_service_message(self, message: Message) -> bool:
        for name in self.SERVICE_MESSAGE_FIELDS:
            if getattr(message, name, None):
                return True
        return False

    def _is_join_message(self, message: Message) -> bool:
        return bool(getattr(message, "new_chat_members", None))

    async def _handle_join_message(self, message: Message) -> None:
        try:
            new_members = message.new_chat_members
            if not new_members:
                log_event(
                    "welcome_skipped",
                    reason="empty_new_chat_members",
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                )
                return

            topic_id = self.welcome_topic_id or config.WELCOME_TOPIC_ID
            forum = bool(getattr(message.chat, "is_forum", False))

            # В форум-супергруппах без топика отправка почти всегда падает (Bad Request);
            # message_thread_id=0 тоже невалиден.
            if forum and not topic_id:
                log_event(
                    "welcome_skipped",
                    reason="forum_without_topic_id",
                    chat_id=message.chat.id,
                    welcome_topic_id=topic_id,
                )
                logger.error(
                    "Группа %s объявлена как форум, но WELCOME_TOPIC_ID=0: "
                    "задайте id топика приветствий в .env (message_thread_id ветки из Telegram/Bot API).",
                    message.chat.id,
                )
                return

            for user in new_members:
                if user.is_bot:
                    logger.info("Bot %s joined, skipping welcome", user.id)
                    continue

                await send_club_member_welcome(
                    self.bot,
                    message.chat.id,
                    user,
                    welcome_topic_id=topic_id or 0,
                    is_forum=forum,
                    telegram_message_id_in=message.message_id,
                )
        except Exception as e:
            log_event(
                "welcome_error",
                chat_id=message.chat.id,
                message_id=message.message_id,
                error=str(e),
            )
            logger.error("Failed to handle join message: %s", e, exc_info=True)

    async def __call__(self, handler, event: Update, data: Dict[str, Any]):
        if not event.message or event.message.chat.id != self.club_group_id:
            return await handler(event, data)

        msg = event.message

        nm = getattr(msg, "new_chat_members", None) or []
        nm_ids = [u.id for u in nm] if nm else []
        log_event(
            "group_message",
            update_id=getattr(event, "update_id", None),
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            message_thread_id=getattr(msg, "message_thread_id", None),
            is_forum=bool(getattr(msg.chat, "is_forum", False)),
            from_user_id=msg.from_user.id if msg.from_user else None,
            has_new_chat_members=bool(nm),
            new_member_ids=nm_ids,
            is_service_hygiene=self._is_service_message(msg),
        )

        if self._is_join_message(msg):
            await self._handle_join_message(msg)

        if self._is_service_message(msg):
            logger.info("Service message detected, deleting...")
            try:
                await msg.delete()
                logger.info(
                    "Deleted service message %s from chat %s",
                    msg.message_id,
                    msg.chat.id,
                )
                log_event(
                    "service_message_deleted",
                    chat_id=msg.chat.id,
                    message_id=msg.message_id,
                    ok=True,
                )
            except Exception as e:
                log_event(
                    "service_message_deleted",
                    chat_id=msg.chat.id,
                    message_id=msg.message_id,
                    ok=False,
                    error=str(e),
                )
                logger.error("Failed to delete service message: %s", e)
            return

        return await handler(event, data)
