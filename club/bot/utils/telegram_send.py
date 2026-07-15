"""Отправка длинных HTML-сообщений в Telegram."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

from aiogram import Bot
from aiogram.enums import ParseMode

from bot.utils.telegram_html import sanitize_telegram_html, split_telegram_html_message_chunks

logger = logging.getLogger(__name__)

TELEGRAM_HTML_CHUNK_LEN = 3800
_RETRY_AFTER_RE = re.compile(r"retry after (\d+)", re.IGNORECASE)


def parse_telegram_retry_after(exc: BaseException) -> Optional[int]:
    m = _RETRY_AFTER_RE.search(str(exc))
    if not m:
        return None
    try:
        return max(1, int(m.group(1)))
    except ValueError:
        return None


async def send_telegram_html_chunks(
    bot: Bot,
    chat_id: int,
    html: str,
    *,
    message_thread_id: Optional[int] = None,
    max_len: int = TELEGRAM_HTML_CHUNK_LEN,
    disable_web_page_preview: bool = True,
    disable_notification: bool = False,
    reply_markup: Any = None,
    reply_to_message_id: Optional[int] = None,
    sanitize: bool = True,
    return_first_message_id: bool = False,
) -> Optional[int]:
    """Отправляет HTML; при превышении лимита режет на части.

    Возвращает ``message_id`` первого или последнего фрагмента (см. ``return_first_message_id``)
    или ``None``.
    ``reply_markup`` — только на последнем фрагменте;
    ``reply_to_message_id`` — только на первом.
    """
    payload = sanitize_telegram_html(html or "") if sanitize else (html or "")
    if not payload.strip():
        return None

    chunks = split_telegram_html_message_chunks(payload, max_len=max_len)
    first_id: Optional[int] = None
    last_id: Optional[int] = None

    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        kwargs: dict[str, Any] = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": ParseMode.HTML,
            "disable_web_page_preview": disable_web_page_preview,
            "disable_notification": disable_notification,
        }
        if message_thread_id:
            kwargs["message_thread_id"] = message_thread_id
        if i == len(chunks) - 1 and reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        if i == 0 and reply_to_message_id is not None:
            kwargs["reply_to_message_id"] = reply_to_message_id
        msg = await bot.send_message(**kwargs)
        mid = int(msg.message_id)
        if first_id is None:
            first_id = mid
        last_id = mid

    if return_first_message_id:
        return first_id
    return last_id


async def call_with_flood_retry(
    coro_factory,
    *,
    max_attempts: int = 4,
    log_prefix: str = "telegram",
) -> Any:
    """Выполняет async-вызов с паузой по ``retry after N`` из Telegram."""
    last_exc: Optional[BaseException] = None
    for attempt in range(max_attempts):
        try:
            return await coro_factory()
        except Exception as exc:
            last_exc = exc
            wait = parse_telegram_retry_after(exc)
            if wait is None or attempt >= max_attempts - 1:
                raise
            logger.warning(
                "[%s] flood control, retry in %ss (attempt %s/%s)",
                log_prefix,
                wait,
                attempt + 1,
                max_attempts,
            )
            await asyncio.sleep(wait)
    if last_exc is not None:
        raise last_exc
    return None
