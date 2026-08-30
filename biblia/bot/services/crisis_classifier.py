"""Классификатор острого кризиса (DEV-1): dict → LLM → fail-safe True."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CACHE_TTL_SEC = 3600.0
_LLM_TIMEOUT_SEC = 4.0
_CACHE: dict[str, tuple[bool, float]] = {}

CRISIS_FAST = re.compile(
    r"погиб|\bумер|умерл|смерт|похорон|утрат|не\s+стало|"
    r"избива|насил|изнасил|суицид|покончить\s+с\s+собой|"
    r"не\s+хочу\s+жить|жить\s+не\s+хочу|"
    r"\bрак\b|онколог|инсульт|инфаркт|реанимац|хоспис|"
    r"развод|измен(?:а|ил|ила|яет)|бросил\s+мен|ушёл\s+к\s+друг|"
    r"уволил|нет\s+денег|не\s+на\s+что\s+жить|нечем\s+платить|"
    r"долг(?:и|ов)\b|коллектор",
    re.IGNORECASE,
)

_SYSTEM = (
    "Ты классификатор. Определи, находится ли человек в остром кризисе.\n\n"
    "Ответь ровно одним словом: crisis или ok. Без пояснений и знаков препинания.\n\n"
    "crisis:\n"
    "- смерть, похороны, тяжёлая утрата\n"
    "- тяжёлая болезнь, госпитализация, диагноз — свой или близкого\n"
    "- насилие, угроза безопасности\n"
    "- измена, разрыв, развод в активной фазе\n"
    "- потеря работы, долги, нехватка денег на еду и жильё\n"
    "- нежелание жить\n\n"
    "ok:\n"
    "- духовный или богословский вопрос\n"
    "- благодарность, радость, хорошая новость\n"
    "- обычная грусть, усталость, сомнение\n"
    "- просьба о молитве без описания беды\n"
    "- бытовой вопрос\n\n"
    "Если сомневаешься — crisis."
)


def _cache_get(key: str) -> Optional[bool]:
    item = _CACHE.get(key)
    if not item:
        return None
    val, exp = item
    if time.monotonic() > exp:
        _CACHE.pop(key, None)
        return None
    return val


def _cache_set(key: str, value: bool) -> None:
    _CACHE[key] = (value, time.monotonic() + _CACHE_TTL_SEC)
    # простая уборка, чтобы не рос бесконечно
    if len(_CACHE) > 5000:
        now = time.monotonic()
        dead = [k for k, (_, e) in _CACHE.items() if e <= now]
        for k in dead[:1000]:
            _CACHE.pop(k, None)


async def _log_moderation(
    user_storage,
    user_id: int,
    event_type: str,
    data: dict,
) -> None:
    if not user_storage or not user_id:
        return
    try:
        await user_storage.log_interaction(
            user_id=int(user_id),
            event_category="moderation",
            event_type=event_type,
            data=data,
        )
    except Exception as e:
        logger.debug("crisis log failed: %s", e)


async def _llm_classify(llm_client: Any, text: str) -> str:
    """Возвращает нормализованный ответ: 'ok' | 'crisis' | ''."""
    user = (text or "")[:2000]
    # AgentsClient.complete_simple / raw chat — пробуем несколько путей
    if hasattr(llm_client, "crisis_classify"):
        raw = await llm_client.crisis_classify(user)
        return (raw or "").strip().lower()

    client = getattr(llm_client, "client", None)
    model = getattr(llm_client, "CHAT_MODEL", None) or "deepseek-v4-flash"
    if client is None:
        raise RuntimeError("llm_client without .client")

    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0,
        max_tokens=3,
    )
    choice = (resp.choices or [None])[0]
    content = ""
    if choice is not None and choice.message is not None:
        content = (choice.message.content or "").strip()
    # usage log best-effort
    try:
        if hasattr(llm_client, "_log_usage"):
            await llm_client._log_usage(
                user_id=0,
                response=resp,
                request_kind="crisis_classifier",
            )
    except Exception:
        pass
    return content.lower().split()[0] if content else ""


async def is_crisis_context(
    user_storage,
    llm_client,
    user_id: int,
    text: str,
    *,
    point: str = "keyboard",
) -> bool:
    """True — коммерческие блоки не показывать."""
    raw = (text or "").strip()
    if not raw:
        return False
    low = raw.casefold()
    if low.startswith("[нажата кнопка") or raw.startswith("/"):
        return False

    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    cache_key = f"crisis:{digest}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    if CRISIS_FAST.search(raw):
        _cache_set(cache_key, True)
        await _log_moderation(
            user_storage,
            user_id,
            "commerce_blocked_crisis",
            {"via": "dict", "point": point},
        )
        return True

    if llm_client is None:
        # без LLM — fail-safe, не кэшируем
        await _log_moderation(
            user_storage,
            user_id,
            "crisis_classifier_error",
            {"error": "no_llm_client"},
        )
        return True

    try:
        answer = await asyncio.wait_for(
            _llm_classify(llm_client, raw),
            timeout=_LLM_TIMEOUT_SEC,
        )
    except Exception as e:
        await _log_moderation(
            user_storage,
            user_id,
            "crisis_classifier_error",
            {"error": str(e)[:200]},
        )
        return True

    if answer == "ok":
        _cache_set(cache_key, False)
        return False
    if answer == "crisis":
        _cache_set(cache_key, True)
        await _log_moderation(
            user_storage,
            user_id,
            "commerce_blocked_crisis",
            {"via": "llm", "point": point},
        )
        return True

    await _log_moderation(
        user_storage,
        user_id,
        "crisis_classifier_error",
        {"error": f"bad_answer:{answer[:40]}"},
    )
    return True
