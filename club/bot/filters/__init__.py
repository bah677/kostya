"""Переиспользуемые фильтры aiogram (router / register_handlers)."""

from aiogram import F

# Входящее message (текст, медиа, команды через message)
PRIVATE_CHAT_ONLY = F.chat.type == "private"

# CallbackQuery от inline-клавиатуры, привязанной к сообщению в нужном чате
PRIVATE_INLINE_CALLBACK_ONLY = F.message.chat.type == "private"

__all__ = ("PRIVATE_CHAT_ONLY", "PRIVATE_INLINE_CALLBACK_ONLY")
