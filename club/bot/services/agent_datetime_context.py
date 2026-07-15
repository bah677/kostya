"""Актуальные дата и время для системного контекста LLM-агентов."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")

_DATETIME_MARKER = "Сейчас:"


def now_msk() -> datetime:
    return datetime.now(MSK)


def format_datetime_context(now: Optional[datetime] = None) -> str:
    """Блок для system prompt: текущие дата/время по МСК."""
    dt = now or now_msk()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MSK)
    else:
        dt = dt.astimezone(MSK)
    weekdays = (
        "понедельник",
        "вторник",
        "среда",
        "четверг",
        "пятница",
        "суббота",
        "воскресенье",
    )
    wd = weekdays[dt.weekday()]
    return (
        f"Сейчас: {dt.strftime('%Y-%m-%d %H:%M')} МСК, {wd}.\n"
        "Относительные слова («вчера», «прошлая пятница», «на прошлой неделе») "
        "считай от этой даты.\n"
        "Не называй конкретный день недели или число, если их нет во фрагментах "
        "RAG и в переписке."
    )


def prepend_datetime_context(system: str, *, now: Optional[datetime] = None) -> str:
    """Добавляет дату/время к system prompt, если блока ещё нет."""
    text = (system or "").strip()
    if not text:
        return format_datetime_context(now)
    if _DATETIME_MARKER in text:
        return text
    return f"{text}\n\n{format_datetime_context(now)}"
