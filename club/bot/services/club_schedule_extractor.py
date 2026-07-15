"""Извлечение событий расписания из текста (LLM)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI

from bot.services.agent_datetime_context import prepend_datetime_context
from bot.texts.prompts.club_schedule_extractor import SCHEDULE_EXTRACTOR_SYSTEM

logger = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")
CHAT_MODEL = "deepseek-chat"

_SCHEDULE_HINT = re.compile(
    r"расписан|эфир|молитв|подкаст|покаян|вопрос.?ответ|перенес|отмен|"
    r"завтра|послезавтра|понедельник|вторник|сред|четверг|пятниц|суббот|воскресен|"
    r"\d{1,2}[:.]\d{2}",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExtractedScheduleEvent:
    action: str
    starts_at: datetime
    ends_at: Optional[datetime]
    title: str
    content_type: str
    is_cancelled: bool


def text_looks_like_schedule(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 8:
        return False
    return bool(_SCHEDULE_HINT.search(t))


def vision_media_text(text: str) -> bool:
    """Текст после Vision/Whisper в медиапроцессоре."""
    t = (text or "").strip().lower()
    return t.startswith(
        ("[фото:", "[изображение", "[анимация", "[документ")
    )


def schedule_topic_input_eligible(text: str) -> bool:
    body = (text or "").strip()
    if len(body) < 8:
        return False
    return text_looks_like_schedule(body) or vision_media_text(body)


def _strip_json_fence(raw: str) -> str:
    t = (raw or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def _parse_dt_iso(val: Any) -> Optional[datetime]:
    if not val:
        return None
    s = str(val).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=MSK)
        return dt.astimezone(MSK)
    except ValueError:
        return None


async def extract_schedule_events_from_text(
    client: AsyncOpenAI,
    text: str,
    *,
    context_label: str = "сообщение",
) -> tuple[List[ExtractedScheduleEvent], float]:
    body = (text or "").strip()
    if not body:
        return [], 0.0

    user_block = (
        f"Источник: {context_label}\n\n"
        f"Текст:\n{body[:8000]}"
    )
    try:
        resp = await client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": prepend_datetime_context(SCHEDULE_EXTRACTOR_SYSTEM)},
                {"role": "user", "content": user_block},
            ],
            temperature=0.15,
            max_tokens=1500,
        )
        raw = resp.choices[0].message.content or ""
        data = json.loads(_strip_json_fence(raw))
    except Exception as e:
        logger.warning("schedule extract failed: %s", e)
        return [], 0.0

    confidence = float(data.get("confidence") or 0.0)
    events_raw = data.get("events") or []
    if not isinstance(events_raw, list):
        return [], confidence

    out: List[ExtractedScheduleEvent] = []
    for item in events_raw:
        if not isinstance(item, dict):
            continue
        starts = _parse_dt_iso(item.get("starts_at_iso"))
        if not starts:
            continue
        action = str(item.get("action") or "upsert").strip().lower()
        title = str(item.get("title") or "Событие клуба").strip()[:500]
        ctype = str(item.get("content_type") or "other").strip().lower()[:32]
        ends = _parse_dt_iso(item.get("ends_at_iso"))
        cancelled = bool(item.get("is_cancelled")) or action == "cancel"
        out.append(
            ExtractedScheduleEvent(
                action=action,
                starts_at=starts,
                ends_at=ends,
                title=title,
                content_type=ctype,
                is_cancelled=cancelled,
            )
        )
    return out, confidence
