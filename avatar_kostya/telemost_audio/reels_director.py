"""Параллельно с нарезкой: режиссёрский бриф Reels по полной расшифровке → топик 1492."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, Optional

from bot.utils.rag_admin_context import rag_reels_brief_chat_topic
from config import config
from telemost_audio.recording_kind import (
    KIND_LABELS,
    recording_kind_from_pending,
    wants_shorts_clips,
)

logger = logging.getLogger(__name__)

_active: set[str] = set()
_TG_CHUNK = 3900
_TRANSCRIPT_CHARS = 280_000

_SYSTEM = """Ты — режиссёр вирусных коротких видео.

Ниша: духовное развитие.
Длина каждого ролика: 30 или 60 секунд. Где материал тянет — делай оба формата.

Правила кадра:
- Первые 2 секунды — хук.
- Середина — напряжение через открытые петли.
- Финал — вывод, который хочется переслать.
- Текст оверлеев на экране, темп подачи, настроение музыки.
- Формат для телефона, без монтажа (одна мысль целиком, вертикаль 9:16).

Не жалей деталей. Для каждого Reels укажи:
1) длительность (30 или 60 сек);
2) таймкоды из расшифровки (start–end);
3) хук (что на экране и в голосе в первые 2 секунды);
4) текст оверлеев по секундам;
5) дословную речь / цитату из эфира для озвучки;
6) темп подачи;
7) настроение музыки;
8) почему это захотят переслать.

Сделай несколько сильных Reels (ориентир 6–10), каждый самодостаточный.
Не выдумывай реплик, которых нет в расшифровке. Пиши по-русски."""


def enqueue_telemost_reels_brief(
    bot_app: Any,
    pending_id: uuid.UUID,
    row: Dict[str, Any],
    meta: Dict[str, Any],
    *,
    recording_kind: str = "",
    force: bool = False,
) -> bool:
    if not getattr(config, "TELEMOST_REELS_BRIEF_ENABLED", True):
        return False
    kind = (recording_kind or "").strip().lower() or recording_kind_from_pending(
        row, meta
    )
    if not wants_shorts_clips(kind):
        return False
    pid = str(pending_id)
    if pid in _active and not force:
        logger.info("telemost_reels_brief: already running pending_id=%s", pid)
        return False
    _active.add(pid)
    asyncio.create_task(
        _run_reels_brief(bot_app, pending_id, row, meta, recording_kind=kind),
        name=f"telemost_reels_brief_{pid[:8]}",
    )
    return True


def _target_topic() -> tuple[int, Optional[int]]:
    chat, topic = rag_reels_brief_chat_topic()
    if not chat or not topic:
        logger.warning(
            "telemost_reels_brief: не задан чат/топик "
            "(TELEMOST_REELS_BRIEF_TOPIC_ID)"
        )
        return 0, None
    return int(chat), int(topic)


async def _ask_openai(*, title: str, kind_label: str, transcript: str) -> str:
    from openai import AsyncOpenAI

    key = (config.OPENAI_API_KEY or "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY не задан")
    model = (
        getattr(config, "TELEMOST_REELS_BRIEF_MODEL", None) or "gpt-4.1"
    ).strip() or "gpt-4.1"
    max_tokens = int(
        getattr(config, "TELEMOST_REELS_BRIEF_MAX_TOKENS", 16000) or 16000
    )
    body = transcript.strip()
    if len(body) > _TRANSCRIPT_CHARS:
        body = body[:_TRANSCRIPT_CHARS] + "\n\n[…расшифровка обрезана по длине…]"
    user = (
        "Используя материал ниже, создавай Reels согласно правилам.\n\n"
        f"Тип записи: {kind_label}\n"
        f"Название: {title}\n\n"
        "Полная расшифровка эфира:\n\n"
        f"{body}"
    )
    client = AsyncOpenAI(api_key=key, timeout=240.0, max_retries=1)
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.7,
            max_tokens=max(2000, min(32000, max_tokens)),
        )
    except Exception as e:
        err = str(e).lower()
        if model != "gpt-4o" and (
            "model" in err and ("not found" in err or "does not exist" in err)
        ):
            logger.warning("telemost_reels_brief: %s недоступна, fallback gpt-4o", model)
            resp = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": user},
                ],
                temperature=0.7,
                max_tokens=max(2000, min(16000, max_tokens)),
            )
        else:
            raise
    choice = resp.choices[0] if resp.choices else None
    text = (
        (getattr(choice.message, "content", None) or "").strip() if choice else ""
    )
    if not text:
        raise RuntimeError("OpenAI вернул пустой ответ")
    return text


def _chunks(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    out: list[str] = []
    rest = raw
    while rest:
        if len(rest) <= _TG_CHUNK:
            out.append(rest)
            break
        cut = rest.rfind("\n", 0, _TG_CHUNK)
        if cut < 800:
            cut = _TG_CHUNK
        out.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return out


async def _run_reels_brief(
    bot_app: Any,
    pending_id: uuid.UUID,
    row: Dict[str, Any],
    meta: Dict[str, Any],
    *,
    recording_kind: str,
) -> None:
    pid = str(pending_id)
    chat_id, topic_id = _target_topic()
    bot = getattr(bot_app, "bot", None)
    title = (
        meta.get("topic_title")
        or meta.get("source")
        or row.get("subject")
        or "Эфир"
    )
    kind_label = KIND_LABELS.get(recording_kind, "Эфир")
    transcript = (row.get("transcript_text") or "").strip()

    try:
        if not bot or not chat_id or not topic_id:
            logger.warning("telemost_reels_brief: no chat/bot/topic")
            return
        if len(transcript) < 200:
            logger.warning(
                "telemost_reels_brief: transcript too short pending=%s", pid
            )
            return

        brief = await _ask_openai(
            title=str(title),
            kind_label=kind_label,
            transcript=transcript,
        )
        kwargs = {"message_thread_id": int(topic_id)}
        for part in _chunks(brief):
            await bot.send_message(chat_id, part[:4096], **kwargs)
            await asyncio.sleep(0.4)
        logger.info(
            "telemost_reels_brief sent pending=%s kind=%s chars=%s",
            pid,
            recording_kind,
            len(brief),
        )
    except Exception as e:
        logger.exception("telemost_reels_brief failed pending=%s: %s", pid, e)
    finally:
        _active.discard(pid)
