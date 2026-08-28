"""DeepSeek: короткая молитва для YouTube Shorts."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from youtube_prayer.compose import (
    ComposeFailureInfo,
    PrayerComposeIncompleteError,
    deepseek_complete,
)
from youtube_prayer.tts_text import (
    normalize_amen_display,
    prayer_text_looks_complete,
    strip_prayer_text,
)
from youtube_prayer.trends import PrayerTopic
from youtube_shorts.prompts import (
    SHORT_PRAYER_COMPOSE_MAX_ATTEMPTS,
    SHORT_PRAYER_COMPOSE_MAX_TOKENS,
    SHORT_PRAYER_COMPOSE_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

_SHORT_MIN_CHARS = 80


@dataclass(frozen=True)
class ShortComposeFailureInfo(ComposeFailureInfo):
    def telegram_text(self, *, label: str, index: int, total: int, day: str) -> str:
        trend = self.trend.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        brief = self.brief.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        reason = self.human_reason().replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return (
            f"⛔ <b>Shorts остановлены — ошибка</b>\n"
            f"{label} · {day} · #{index}/{total}\n\n"
            f"<b>Тема:</b> {trend}\n"
            f"<b>Бриф:</b> {brief}\n\n"
            f"Короткая молитва не сгенерирована после {self.attempts} попыток.\n"
            f"<b>Причина:</b> {reason}.\n"
            f"Последний ответ: {self.last_chars} симв., finish={self.last_finish or '?'}\n\n"
            f"TTS и видео для этого шортса <b>не собирались</b>."
        )


class ShortComposeIncompleteError(PrayerComposeIncompleteError):
    pass


def _short_text_looks_complete(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < _SHORT_MIN_CHARS:
        return False
    return prayer_text_looks_complete(t, lang="ru")


async def compose_short_prayer_for_topic(topic: PrayerTopic) -> str:
    """Короткая молитва 1–2 мин для отдельного вертикального Short."""
    system = SHORT_PRAYER_COMPOSE_SYSTEM_PROMPT
    user_base = (
        "Составь короткую личную молитву по этому запросу. "
        "Только текст молитвы, без преамбулы.\n\n"
        f"Тема тренда: {topic.trend}\n"
        f"О чём молиться: {topic.brief}"
    )
    retry_nudge = (
        "\n\nВАЖНО: предыдущий ответ оборвался. "
        "Напиши молитву ПОЛНОСТЬЮ до «Во имя Иисуса Христа, Аминь». "
        "Коротко — 1–2 минуты озвучки."
    )
    last: Optional[str] = None
    last_finish: Optional[str] = None
    last_truncated = False
    last_missing_amen = True

    for attempt in range(1, SHORT_PRAYER_COMPOSE_MAX_ATTEMPTS + 1):
        if attempt >= SHORT_PRAYER_COMPOSE_MAX_ATTEMPTS:
            thinking, effort = "disabled", None
        else:
            thinking, effort = "enabled", "low"
        prompt_user = user_base if attempt == 1 else (user_base + retry_nudge)
        raw, finish = await deepseek_complete(
            system,
            prompt_user,
            temperature=0.5,
            max_tokens=SHORT_PRAYER_COMPOSE_MAX_TOKENS,
            thinking=thinking,
            reasoning_effort=effort,
        )
        text = strip_prayer_text(raw or "")
        if text:
            last = text
        truncated = (finish or "").lower() == "length"
        if text:
            last_finish = finish
            last_truncated = truncated
            last_missing_amen = not _short_text_looks_complete(text)
        ok = bool(text) and _short_text_looks_complete(text) and not truncated
        if ok:
            logger.info(
                "short compose ok trend=%r attempt=%s chars=%s",
                topic.trend,
                attempt,
                len(text),
            )
            return normalize_amen_display(text)
        logger.warning(
            "short compose incomplete trend=%r attempt=%s finish=%s chars=%s",
            topic.trend,
            attempt,
            finish,
            len(text or ""),
        )
        if attempt < SHORT_PRAYER_COMPOSE_MAX_ATTEMPTS:
            await asyncio.sleep(0.4 * attempt)

    info = ShortComposeFailureInfo(
        lang="ru",
        trend=topic.trend,
        brief=topic.brief,
        attempts=SHORT_PRAYER_COMPOSE_MAX_ATTEMPTS,
        last_chars=len(last or ""),
        last_finish=last_finish,
        missing_amen=last_missing_amen,
        truncated=last_truncated,
    )
    raise ShortComposeIncompleteError(info)
