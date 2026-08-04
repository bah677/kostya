"""DeepSeek: фильтр трендов + compose молитвы (логика как в Библии B)."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from openai import AsyncOpenAI

from youtube_prayer.prompts import (
    PRAYER_COMPOSE_MAX_ATTEMPTS,
    PRAYER_COMPOSE_MAX_TOKENS_B,
    PRAYER_COMPOSE_SYSTEM_PROMPT_B,
)
from youtube_prayer.tts_text import (
    prayer_text_looks_complete,
    strip_prayer_text,
)
from youtube_prayer.trends import PrayerTopic

logger = logging.getLogger(__name__)

_WAIT_SEC = 120.0


def _client() -> AsyncOpenAI:
    key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY не задан")
    return AsyncOpenAI(
        api_key=key,
        base_url="https://api.deepseek.com/v1",
        timeout=150.0,
        max_retries=2,
    )


async def deepseek_complete(
    system_prompt: str,
    user_content: str,
    *,
    temperature: float = 0.55,
    max_tokens: int = 2048,
    thinking: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Возвращает (text, finish_reason)."""
    client = _client()
    kwargs = {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    if thinking:
        kwargs["extra_body"] = {"thinking": {"type": thinking}}
    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(**kwargs),
            timeout=_WAIT_SEC,
        )
    except Exception as e:
        logger.error("deepseek_complete failed: %s", e)
        return None, None
    choice = resp.choices[0] if resp.choices else None
    text = None
    finish = None
    if choice:
        finish = getattr(choice, "finish_reason", None)
        msg = choice.message
        text = (getattr(msg, "content", None) or "").strip() or None
    return text, str(finish) if finish else None


async def compose_prayer_for_topic(topic: PrayerTopic) -> Optional[str]:
    """Молитва variant B + retry при обрыве (как personal_prayer)."""
    user_base = (
        "Составь личную молитву по этому запросу человека. "
        "Только текст молитвы, без преамбулы.\n\n"
        f"Тема тренда: {topic.trend}\n"
        f"О чём молиться: {topic.brief}"
    )
    retry_nudge = (
        "\n\nВАЖНО: предыдущий ответ оборвался на середине. "
        "Напиши молитву ПОЛНОСТЬЮ до финала "
        "«Во имя Иисуса Христа, амИнь». Без длинных рассуждений — сразу текст молитвы."
    )
    last: Optional[str] = None
    for attempt in range(1, PRAYER_COMPOSE_MAX_ATTEMPTS + 1):
        if attempt >= PRAYER_COMPOSE_MAX_ATTEMPTS:
            thinking, effort = "disabled", None
        else:
            thinking, effort = "enabled", "low"
        prompt_user = user_base if attempt == 1 else (user_base + retry_nudge)
        raw, finish = await deepseek_complete(
            PRAYER_COMPOSE_SYSTEM_PROMPT_B,
            prompt_user,
            temperature=0.55,
            max_tokens=PRAYER_COMPOSE_MAX_TOKENS_B,
            thinking=thinking,
            reasoning_effort=effort,
        )
        text = strip_prayer_text(raw or "")
        if text:
            last = text
        truncated = (finish or "").lower() == "length"
        ok = bool(text) and prayer_text_looks_complete(text) and not truncated
        if ok:
            logger.info(
                "compose ok trend=%r attempt=%s chars=%s",
                topic.trend,
                attempt,
                len(text),
            )
            return text
        logger.warning(
            "compose incomplete trend=%r attempt=%s finish=%s chars=%s",
            topic.trend,
            attempt,
            finish,
            len(text or ""),
        )
        if attempt < PRAYER_COMPOSE_MAX_ATTEMPTS:
            await asyncio.sleep(0.4 * attempt)
    return last
