"""Доводит текст рассылки до содержательного Telegram HTML: нормализация + повторный запрос к LLM."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from bot.utils.telegram_html import looks_like_telegram_html
from bot.utils.telegram_html_async import normalize_llm_reply_for_telegram_async

if TYPE_CHECKING:
    from openai_client.agents_client import AgentsClient

logger = logging.getLogger(__name__)

MAILING_HTML_USER_ID = 0
DEFAULT_MAX_ATTEMPTS = 3

STRICT_HTML_TAIL_FOR_PROMPT = (
    "\n\nКРИТИЧНО: ответ только с разметкой Telegram HTML — минимум один тег из набора "
    "<b>, <i>, <blockquote>, <a href=\"…\">. Без «голого» текста и без Markdown."
)


async def ensure_llm_text_telegram_html(
    fetch_raw: Callable[[bool], Awaitable[Optional[str]]],
    *,
    agents_client: "AgentsClient",
    log_context: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Optional[str]:
    """
    Для каждой попытки: ``fetch_raw(strict)`` → async-нормализация (в т.ч. DeepSeek format).
    Если после нормализации нет содержательного HTML — следующая попытка со ``strict=True``.
    """
    for attempt in range(1, max_attempts + 1):
        strict = attempt > 1
        raw = await fetch_raw(strict)
        text = (raw or "").strip()
        if not text:
            logger.warning("%s: пустой сырой текст (attempt %s)", log_context, attempt)
            continue
        normalized = await normalize_llm_reply_for_telegram_async(
            text,
            user_id=MAILING_HTML_USER_ID,
            agents_client=agents_client,
        )
        out = (normalized or "").strip()
        if looks_like_telegram_html(out):
            return out
        logger.warning(
            "%s: нет содержательного HTML после normalize (attempt %s), snippet=%r",
            log_context,
            attempt,
            out[:240],
        )
    return None
