"""Имя бота в Telegram (username) для ссылок — из getMe по токену, без хардкода."""

from __future__ import annotations

import logging
from typing import Dict, Optional

from aiogram import Bot

logger = logging.getLogger(__name__)

# token → username (без @)
_USERNAME_BY_TOKEN: Dict[str, str] = {}


def _normalize_bot_username(raw: Optional[str]) -> Optional[str]:
    if not raw or not isinstance(raw, str):
        return None
    u = raw.strip().lstrip("@")
    return u or None


async def resolve_bot_username(
    bot: Optional[Bot] = None,
    *,
    token: str = "",
) -> Optional[str]:
    """
    Username бота для ``https://t.me/<username>``.

    Берём только из Telegram API (``getMe``) по живому ``Bot`` или по ``token``.
    Результат кэшируется на процесс.
    """
    tok = (token or "").strip()
    if bot is not None and not tok:
        tok = (getattr(bot, "token", None) or "").strip()
    if tok and tok in _USERNAME_BY_TOKEN:
        return _USERNAME_BY_TOKEN[tok]

    own_session = False
    client = bot
    if client is None:
        if not tok:
            return None
        client = Bot(token=tok)
        own_session = True

    try:
        me = await client.get_me()
        name = _normalize_bot_username(getattr(me, "username", None))
        if name and tok:
            _USERNAME_BY_TOKEN[tok] = name
        return name
    except Exception as e:
        logger.error("resolve_bot_username getMe failed: %s", e)
        return None
    finally:
        if own_session and client is not None:
            await client.session.close()


async def resolve_telegram_bot_username(bot: Bot) -> Optional[str]:
    """Username основного бота (аватар) через getMe."""
    return await resolve_bot_username(bot)


async def resolve_telegram_bot_display_name(bot: Bot) -> str:
    """
    Отображаемое имя бота (поле Name в BotFather), не @username.

    ``User.first_name`` в ответе ``getMe()`` для бота — это как раз bot name.
    """
    try:
        me = await bot.get_me()
        if me:
            first = (getattr(me, "first_name", None) or "").strip()
            if first:
                return first
            last = (getattr(me, "last_name", None) or "").strip()
            if last:
                return last
    except Exception as e:
        logger.error("resolve_telegram_bot_display_name: get_me failed: %s", e)
    return "аватар"
