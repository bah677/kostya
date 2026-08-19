"""Параллельно с нарезкой: режиссёрские сценарии Reels по полной расшифровке → топик 1492.

Алгоритм двухшаговый:
  1. Из расшифровки извлекаем главные мысли (STEP_EXTRACT).
  2. Для каждой мысли строим отдельный сценарий Reels (STEP_SCENARIO).
     Каждый сценарий — отдельное сообщение в топик.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

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

# ── Шаг 1: извлечение главных мыслей ──────────────────────────────────────────
_SYSTEM_EXTRACT = """Ты — редактор контента. Твоя задача — внимательно прочитать расшифровку эфира и выписать из неё все сильные, самодостаточные мысли, подходящие для вирусного короткого видео (Reels/Shorts).

Ниша: духовное развитие.

Критерии мысли:
- Вызывает узнавание («это про меня»), желание переслать или сохранить.
- Самодостаточна — понятна без контекста всего эфира.
- Конкретная, не абстрактная.
- Основана на реальных словах из расшифровки (не придумывать).

Формат ответа — нумерованный список. Каждый пункт:
№. [Краткий заголовок мысли] | [Дословная цитата или парафраз из расшифровки, 1–3 предложения]

Пиши по-русски. Выдай все мысли, которые нашёл (ориентир 6–12)."""

# ── Шаг 2: сценарий Reels по одной мысли ──────────────────────────────────────
_SYSTEM_SCENARIO = """Ты — режиссёр вирусных коротких видео.

Ниша: духовное развитие.
Длина ролика: 30 или 60 секунд (выбери сам, исходя из глубины мысли).

Правила кадра:
- Первые 2 секунды — хук (что на экране и что говорится в голосе).
- Середина — напряжение через открытые петли.
- Финал — вывод, который хочется переслать.
- Формат для телефона, вертикаль 9:16, без монтажа (одна мысль целиком).

Структура твоего ответа:
1) Длительность: 30 или 60 сек
2) Таймкоды из расшифровки (start–end, если есть)
3) Хук: что на экране + первые слова голоса
4) Текст оверлеев по секундам
5) Цитата / дословная речь из эфира для озвучки
6) Темп подачи
7) Настроение музыки
8) Почему это захотят переслать

Не выдумывай реплик, которых нет в исходнике. Пиши по-русски."""


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


async def run_reels_brief_for_row(
    bot_app: Any,
    row: Dict[str, Any],
    *,
    recording_kind: str = "",
) -> None:
    """Точка входа для ручного запуска из /reels-команды."""
    pending_id = row.get("id") or uuid.uuid4()
    if not isinstance(pending_id, uuid.UUID):
        try:
            pending_id = uuid.UUID(str(pending_id))
        except Exception:
            pending_id = uuid.uuid4()
    clf = row.get("classification") or {}
    meta: Dict[str, Any] = {}
    if isinstance(clf, dict):
        meta = clf.get("extra") or {}
    kind = (recording_kind or "").strip().lower() or recording_kind_from_pending(
        row, meta
    )
    await _run_reels_brief(bot_app, pending_id, row, meta, recording_kind=kind)


def _target_topic() -> tuple[int, Optional[int]]:
    chat, topic = rag_reels_brief_chat_topic()
    if not chat or not topic:
        logger.warning(
            "telemost_reels_brief: не задан чат/топик "
            "(TELEMOST_REELS_BRIEF_TOPIC_ID)"
        )
        return 0, None
    return int(chat), int(topic)


def _get_openai_model() -> str:
    return (
        (getattr(config, "TELEMOST_REELS_BRIEF_MODEL", None) or "gpt-4.1").strip()
        or "gpt-4.1"
    )


def _get_max_tokens() -> int:
    return int(getattr(config, "TELEMOST_REELS_BRIEF_MAX_TOKENS", 16000) or 16000)


async def _call_openai(
    client: Any,
    *,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
) -> str:
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.7,
            max_tokens=max(2000, min(32000, max_tokens)),
        )
    except Exception as e:
        err = str(e).lower()
        if model != "gpt-4o" and "model" in err and (
            "not found" in err or "does not exist" in err
        ):
            logger.warning("telemost_reels_brief: %s недоступна, fallback gpt-4o", model)
            resp = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system},
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


async def _extract_ideas(
    client: Any, *, title: str, kind_label: str, transcript: str, model: str, max_tokens: int
) -> str:
    """Шаг 1: извлекаем список главных мыслей."""
    body = transcript.strip()
    if len(body) > _TRANSCRIPT_CHARS:
        body = body[:_TRANSCRIPT_CHARS] + "\n\n[…расшифровка обрезана по длине…]"
    user = (
        f"Тип записи: {kind_label}\n"
        f"Название: {title}\n\n"
        "Полная расшифровка эфира:\n\n"
        f"{body}"
    )
    return await _call_openai(
        client, model=model, system=_SYSTEM_EXTRACT, user=user,
        max_tokens=min(4000, max_tokens),
    )


async def _build_scenario(
    client: Any,
    *,
    idea_text: str,
    title: str,
    kind_label: str,
    model: str,
    max_tokens: int,
) -> str:
    """Шаг 2: строим сценарий Reels по одной мысли."""
    user = (
        f"Тип записи: {kind_label}\n"
        f"Название эфира: {title}\n\n"
        f"Мысль для Reels:\n{idea_text}\n\n"
        "Напиши детальный сценарий Reels для этой мысли."
    )
    return await _call_openai(
        client, model=model, system=_SYSTEM_SCENARIO, user=user,
        max_tokens=min(max_tokens, 4000),
    )


def _parse_ideas(raw: str) -> List[str]:
    """Разбиваем ответ шага 1 на отдельные мысли."""
    ideas: List[str] = []
    current: List[str] = []
    for line in raw.splitlines():
        # Новый пункт начинается с цифры (1. / 2. / 1) и т.д.)
        if re.match(r"^\s*\d+[\.\)]\s+", line) and current:
            ideas.append("\n".join(current).strip())
            current = [line.strip()]
        else:
            current.append(line.strip())
    if current:
        ideas.append("\n".join(current).strip())
    return [i for i in ideas if i]


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
    from openai import AsyncOpenAI

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

        key = (config.OPENAI_API_KEY or "").strip()
        if not key:
            raise RuntimeError("OPENAI_API_KEY не задан")
        model = _get_openai_model()
        max_tokens = _get_max_tokens()
        client = AsyncOpenAI(api_key=key, timeout=240.0, max_retries=1)
        kwargs = {"message_thread_id": int(topic_id)}
        title_str = str(title)

        # ── Шаг 1: извлекаем главные мысли ──
        ideas_raw = await _extract_ideas(
            client,
            title=title_str,
            kind_label=kind_label,
            transcript=transcript,
            model=model,
            max_tokens=max_tokens,
        )
        ideas = _parse_ideas(ideas_raw)
        if not ideas:
            # Если парсер не распознал структуру — работаем с цельным текстом
            ideas = [ideas_raw]

        logger.info(
            "telemost_reels_brief: extracted %d ideas pending=%s",
            len(ideas), pid,
        )

        # Шапка с заголовком в топик
        header = f"🎬 <b>Reels-сценарии</b> · {kind_label}: {title_str}\n({len(ideas)} мыслей)"
        await bot.send_message(chat_id, header, parse_mode="HTML", **kwargs)
        await asyncio.sleep(0.5)

        # ── Шаг 2: для каждой мысли — отдельный сценарий ──
        for idx, idea in enumerate(ideas, 1):
            scenario = await _build_scenario(
                client,
                idea_text=idea,
                title=title_str,
                kind_label=kind_label,
                model=model,
                max_tokens=max_tokens,
            )
            # Нумерованный заголовок сценария
            first_line = idea.splitlines()[0][:120] if idea else ""
            msg_header = f"<b>#{idx}</b> {first_line}\n\n"
            full_text = msg_header + scenario
            for part in _chunks(full_text):
                await bot.send_message(chat_id, part[:4096], parse_mode="HTML", **kwargs)
                await asyncio.sleep(0.4)
            await asyncio.sleep(0.6)

        logger.info(
            "telemost_reels_brief sent pending=%s kind=%s ideas=%d",
            pid, recording_kind, len(ideas),
        )
    except Exception as e:
        logger.exception("telemost_reels_brief failed pending=%s: %s", pid, e)
    finally:
        _active.discard(pid)
