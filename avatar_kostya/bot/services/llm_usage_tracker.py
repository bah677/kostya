"""Единый учёт расходов LLM/TTS/images → таблица ``token_usage``.

Подключается при старте бота через ``configure()``. Фоновые пайплайны
(Shorts, Reels, TTS) пишут на SYSTEM user_id (SUPER_ADMIN_ID или 1).

ElevenLabs: в ``prompt_tokens`` кладём число символов текста (условная
единица биллинга EL), model = model_id, request_kind = tts_*.
Images: request_kind = image_generation, токены 0 если API не отдал usage.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_storage: Any = None
_default_user_id: int = 1
_configured: bool = False
_lazy_lock: Optional[asyncio.Lock] = None


def configure(user_storage: Any, *, default_user_id: Optional[int] = None) -> None:
    """Вызвать один раз при старте бота (после user_storage.initialize)."""
    global _storage, _default_user_id, _configured
    _storage = user_storage
    uid = int(default_user_id or 0)
    if uid <= 0:
        try:
            from config import config

            uid = int(getattr(config, "SUPER_ADMIN_ID", 0) or 0)
        except Exception:
            uid = 0
    _default_user_id = uid if uid > 0 else 1
    _configured = True
    logger.info(
        "llm_usage_tracker configured default_user_id=%s", _default_user_id
    )


async def _lazy_bootstrap_storage() -> Any:
    """Для скриптов вне бота: одноразово поднять UserStorage."""
    global _storage, _lazy_lock, _configured, _default_user_id
    if _storage is not None:
        return _storage
    if _lazy_lock is None:
        _lazy_lock = asyncio.Lock()
    async with _lazy_lock:
        if _storage is not None:
            return _storage
        try:
            from config import config, load_biblia_bot_config
            from storage.user_storage import UserStorage

            bc = load_biblia_bot_config()
            st = UserStorage(bc.database_url)
            await st.connect()
            _storage = st
            uid = int(getattr(config, "SUPER_ADMIN_ID", 0) or 0)
            _default_user_id = uid if uid > 0 else 1
            _configured = True
            logger.info("llm_usage_tracker lazy-bootstrap ok user=%s", _default_user_id)
            return _storage
        except Exception as e:
            logger.warning("llm_usage lazy-bootstrap failed: %s", e)
            return None


def _resolve_user_id(user_id: Optional[int]) -> int:
    if user_id is not None and int(user_id) > 0:
        return int(user_id)
    return _default_user_id


async def _ensure_user(storage: Any, user_id: int) -> None:
    try:
        async with storage.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO users (user_id, username, first_name, is_active)
                VALUES ($1, 'system_llm_usage', 'LLM usage', TRUE)
                ON CONFLICT (user_id) DO NOTHING
                """,
                user_id,
            )
    except Exception as e:
        logger.debug("llm_usage ensure user: %s", e)


async def log_chat_usage(
    *,
    provider: str,
    model: str,
    usage: Any = None,
    request_kind: str = "chat_completion",
    user_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    duration_sec: Optional[int] = None,
) -> None:
    """Пишет usage от chat.completions / responses. Не бросает наружу."""
    storage = _storage
    if storage is None:
        storage = await _lazy_bootstrap_storage()
    if storage is None:
        logger.debug(
            "llm_usage skip (not configured): %s/%s/%s",
            provider,
            model,
            request_kind,
        )
        return
    uid = _resolve_user_id(user_id)
    try:
        await _ensure_user(storage, uid)
        await storage.log_llm_completion_usage(
            user_id=uid,
            provider=provider,
            model=model or "unknown",
            usage=usage,
            request_kind=request_kind,
            request_id=str(uuid.uuid4()),
            duration_sec=duration_sec,
            metadata=metadata,
        )
    except Exception as e:
        logger.warning(
            "llm_usage log failed %s/%s/%s: %s",
            provider,
            model,
            request_kind,
            e,
        )


def schedule_chat_usage(**kwargs: Any) -> None:
    """Fire-and-forget из sync-контекста или когда await неудобен."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(log_chat_usage(**kwargs))


async def log_from_response(
    resp: Any,
    *,
    provider: str,
    model: Optional[str] = None,
    request_kind: str = "chat_completion",
    user_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    duration_sec: Optional[int] = None,
) -> None:
    mid = model or getattr(resp, "model", None) or "unknown"
    usage = getattr(resp, "usage", None)
    await log_chat_usage(
        provider=provider,
        model=str(mid),
        usage=usage,
        request_kind=request_kind,
        user_id=user_id,
        metadata=metadata,
        duration_sec=duration_sec,
    )


async def logged_chat_create(
    client: Any,
    *,
    provider: str,
    request_kind: str,
    user_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    **create_kwargs: Any,
) -> Any:
    """Обёртка над ``client.chat.completions.create`` с записью usage."""
    import time

    t0 = time.monotonic()
    resp = await client.chat.completions.create(**create_kwargs)
    dur = int(time.monotonic() - t0)
    model = str(create_kwargs.get("model") or getattr(resp, "model", None) or "unknown")
    await log_from_response(
        resp,
        provider=provider,
        model=model,
        request_kind=request_kind,
        user_id=user_id,
        metadata=metadata,
        duration_sec=dur,
    )
    return resp


async def log_elevenlabs_tts(
    *,
    model_id: str,
    chars: int,
    voice_id: str = "",
    request_kind: str = "tts",
    user_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    duration_sec: Optional[int] = None,
) -> None:
    """ElevenLabs биллится по символам → prompt_tokens = chars."""
    storage = _storage
    if storage is None:
        storage = await _lazy_bootstrap_storage()
    if storage is None:
        return
    uid = _resolve_user_id(user_id)
    meta = dict(metadata or {})
    meta.setdefault("unit", "characters")
    meta.setdefault("chars", int(chars))
    if voice_id:
        meta.setdefault("voice_id", voice_id)
    try:
        await _ensure_user(storage, uid)
        await storage.add_token_usage_with_metadata(
            uid,
            model_id or "elevenlabs",
            int(chars),
            0,
            int(chars),
            request_id=str(uuid.uuid4()),
            duration_sec=duration_sec,
            metadata=meta,
            provider="elevenlabs",
            request_kind=request_kind,
            raw_usage=json.dumps({"characters": int(chars), "voice_id": voice_id}),
        )
    except Exception as e:
        logger.warning("llm_usage elevenlabs failed: %s", e)


async def log_image_generation(
    *,
    model: str,
    request_kind: str = "image_generation",
    user_id: Optional[int] = None,
    n: int = 1,
    usage: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
    duration_sec: Optional[int] = None,
) -> None:
    storage = _storage
    if storage is None:
        storage = await _lazy_bootstrap_storage()
    if storage is None:
        return
    uid = _resolve_user_id(user_id)
    meta = dict(metadata or {})
    meta.setdefault("n", int(n))
    try:
        await _ensure_user(storage, uid)
        if usage is not None:
            await storage.log_llm_completion_usage(
                user_id=uid,
                provider="openai",
                model=model or "gpt-image",
                usage=usage,
                request_kind=request_kind,
                request_id=str(uuid.uuid4()),
                duration_sec=duration_sec,
                metadata=meta,
            )
        else:
            await storage.add_token_usage_with_metadata(
                uid,
                model or "gpt-image",
                0,
                0,
                0,
                request_id=str(uuid.uuid4()),
                duration_sec=duration_sec,
                metadata=meta,
                provider="openai",
                request_kind=request_kind,
                raw_usage=json.dumps({"images": int(n)}),
            )
    except Exception as e:
        logger.warning("llm_usage image failed: %s", e)


async def log_embedding_usage(
    *,
    model: str,
    usage: Any = None,
    request_kind: str = "embedding",
    user_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    await log_chat_usage(
        provider="openai",
        model=model or "text-embedding-3-small",
        usage=usage,
        request_kind=request_kind,
        user_id=user_id,
        metadata=metadata,
    )
