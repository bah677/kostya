"""Генерация короткого названия просьбы для кнопки в списке ДДД."""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

from openai import AsyncOpenAI

from bot.services.agent_datetime_context import prepend_datetime_context
from bot.texts import ru_wish_board as wb_txt
from bot.texts.prompts.wish_title import WISH_TITLE_SYSTEM

logger = logging.getLogger(__name__)

CHAT_MODEL = "deepseek-chat"
_MAX_LEN = 48


def _fallback_title(description: str, gift_type: str) -> str:
    desc = re.sub(r"\s+", " ", (description or "").strip())
    if desc:
        snippet = desc[:_MAX_LEN]
        if len(desc) > _MAX_LEN:
            snippet = snippet[: _MAX_LEN - 1].rstrip() + "…"
        return snippet
    label = wb_txt.GIFT_TYPE_LABELS.get(gift_type or "", "Просьба о помощи")
    return label[:_MAX_LEN]


def _normalize_title(raw: str) -> str:
    t = re.sub(r"\s+", " ", (raw or "").strip().strip("\"'«»"))
    t = re.sub(r"^#\d+\s*", "", t)
    if len(t) > _MAX_LEN:
        t = t[: _MAX_LEN - 1].rstrip() + "…"
    return t


async def generate_wish_button_title(
    *,
    description: str,
    gift_type: str,
    llm_client: Optional[AsyncOpenAI] = None,
) -> str:
    """Короткая подпись для inline-кнопки; при ошибке LLM — обрезка текста просьбы."""
    fallback = _fallback_title(description, gift_type)
    client = llm_client
    if client is None:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            return fallback
        client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com/v1",
            timeout=20.0,
            max_retries=1,
        )

    gtype = wb_txt.GIFT_TYPE_LABELS.get(gift_type or "", gift_type or "")
    user_block = (
        f"Тип: {gtype}\n\n"
        f"Текст просьбы:\n{(description or '')[:1200]}"
    )
    try:
        response = await client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": prepend_datetime_context(WISH_TITLE_SYSTEM)},
                {"role": "user", "content": user_block},
            ],
            temperature=0.4,
            max_tokens=64,
        )
        raw = (response.choices[0].message.content or "").strip()
        title = _normalize_title(raw)
        return title if len(title) >= 3 else fallback
    except Exception as e:
        logger.warning("wish button title LLM failed: %s", e)
        return fallback
