"""Провайдер и модель ИИ для шагов Контент завода (plan / distill / write)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from course.llm import is_deepseek_model

logger = logging.getLogger(__name__)

LLM_SETTINGS_KEY = "web_llm_models"
_MODELS_CACHE_TTL_SEC = 600.0

# Шаги пайплайна веб-студии.
LLM_STEPS: List[Dict[str, str]] = [
    {
        "id": "plan",
        "title": "План поиска",
        "hint": "Дешёвая модель: какие запросы строить по базе и что искать в сырье.",
    },
    {
        "id": "distill",
        "title": "Выжимка сырья",
        "hint": "Читает длинные исходники и выписывает цитаты под задачу.",
    },
    {
        "id": "write",
        "title": "Генерация ответа",
        "hint": "Финальная модель: собирает контент из паспортов, скилла, этапа и материалов.",
    },
]

PROVIDERS: List[Dict[str, str]] = [
    {"id": "openai", "title": "OpenAI"},
    {"id": "deepseek", "title": "DeepSeek"},
]

# Запасной каталог, если API моделей недоступен.
PROVIDER_MODELS_FALLBACK: Dict[str, List[Dict[str, str]]] = {
    "openai": [
        {"id": "gpt-4o-mini", "title": "gpt-4o-mini"},
        {"id": "gpt-4o", "title": "gpt-4o"},
        {"id": "gpt-4.1-mini", "title": "gpt-4.1-mini"},
        {"id": "gpt-4.1", "title": "gpt-4.1"},
    ],
    "deepseek": [
        {"id": "deepseek-v4-flash", "title": "deepseek-v4-flash"},
        {"id": "deepseek-chat", "title": "deepseek-chat"},
        {"id": "deepseek-reasoner", "title": "deepseek-reasoner"},
        {"id": "deepseek-v4-pro", "title": "deepseek-v4-pro"},
    ],
}

# Совместимость со старым именем.
PROVIDER_MODELS = PROVIDER_MODELS_FALLBACK

_models_cache: Dict[str, Any] = {"ts": 0.0, "models": None, "source": {}}


def _env_defaults() -> Dict[str, Dict[str, str]]:
    from config import config

    plan = str(getattr(config, "WEB_PLANNER_MODEL", "") or "gpt-4o-mini").strip()
    distill = str(getattr(config, "WEB_DISTILL_MODEL", "") or "gpt-4o-mini").strip()
    write = str(
        getattr(config, "WEB_WRITER_MODEL", "")
        or getattr(config, "CONTENT_WRITER_MODEL", "")
        or "deepseek-v4-flash"
    ).strip()
    return {
        "plan": _pair(plan),
        "distill": _pair(distill),
        "write": _pair(write),
    }


def provider_for_model(model: str) -> str:
    return "deepseek" if is_deepseek_model(model) else "openai"


def _pair(model: str) -> Dict[str, str]:
    m = (model or "").strip()
    return {"provider": provider_for_model(m), "model": m}


def decode_llm_settings(raw: Any) -> Dict[str, Dict[str, str]]:
    if isinstance(raw, (bytes, memoryview)):
        raw = bytes(raw).decode("utf-8")
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return {}
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for step in ("plan", "distill", "write"):
        row = raw.get(step)
        if isinstance(row, str):
            model = row.strip()
            if model:
                out[step] = _pair(model)
            continue
        if not isinstance(row, dict):
            continue
        model = str(row.get("model") or "").strip()
        if not model:
            continue
        prov = str(row.get("provider") or "").strip().lower()
        if prov not in ("openai", "deepseek"):
            prov = provider_for_model(model)
        if provider_for_model(model) != prov:
            prov = provider_for_model(model)
        out[step] = {"provider": prov, "model": model[:120]}
    return out


def _keep_openai_model(model_id: str) -> bool:
    mid = (model_id or "").strip().lower()
    if not mid:
        return False
    # Чат-модели; отсекаем embeddings / audio / realtime / image.
    bad = (
        "embed",
        "whisper",
        "tts",
        "davinci",
        "babbage",
        "realtime",
        "audio",
        "transcribe",
        "image",
        "moderation",
        "search",
        "computer-use",
        "codex",
    )
    if any(x in mid for x in bad):
        return False
    return (
        mid.startswith("gpt-")
        or mid.startswith("o1")
        or mid.startswith("o3")
        or mid.startswith("o4")
        or mid.startswith("chatgpt")
    )


def _keep_deepseek_model(model_id: str) -> bool:
    mid = (model_id or "").strip().lower()
    return mid.startswith("deepseek")


def _merge_catalog(
    fallback: List[Dict[str, str]], live: List[str]
) -> List[Dict[str, str]]:
    """Сначала привычные из fallback, потом остальные с API."""
    seen: set[str] = set()
    out: List[Dict[str, str]] = []
    for row in fallback:
        mid = str(row.get("id") or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append({"id": mid, "title": str(row.get("title") or mid)})
    for mid in sorted(live):
        if mid in seen:
            continue
        seen.add(mid)
        out.append({"id": mid, "title": mid})
    return out


async def _fetch_openai_model_ids() -> Tuple[List[str], str]:
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        return [], "no_key"
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=key, timeout=20.0, max_retries=1)
        page = await client.models.list()
        ids = [
            str(m.id)
            for m in (page.data or [])
            if _keep_openai_model(str(getattr(m, "id", "") or ""))
        ]
        return ids, "api" if ids else "empty"
    except Exception as e:
        logger.warning("openai models.list: %s", e)
        return [], "error"


async def _fetch_deepseek_model_ids() -> Tuple[List[str], str]:
    key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        return [], "no_key"
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=key,
            base_url="https://api.deepseek.com/v1",
            timeout=20.0,
            max_retries=1,
        )
        page = await client.models.list()
        ids = [
            str(m.id)
            for m in (page.data or [])
            if _keep_deepseek_model(str(getattr(m, "id", "") or ""))
        ]
        return ids, "api" if ids else "empty"
    except Exception as e:
        logger.warning("deepseek models.list: %s", e)
        return [], "error"


async def fetch_provider_models(*, force: bool = False) -> Dict[str, Any]:
    """Живые списки моделей с кэшем; при сбое — fallback."""
    now = time.monotonic()
    cached = _models_cache.get("models")
    if (
        not force
        and cached is not None
        and now - float(_models_cache.get("ts") or 0) < _MODELS_CACHE_TTL_SEC
    ):
        return {
            "models": cached,
            "source": dict(_models_cache.get("source") or {}),
            "cached": True,
        }

    openai_ids, openai_src = await _fetch_openai_model_ids()
    deepseek_ids, deepseek_src = await _fetch_deepseek_model_ids()

    models = {
        "openai": _merge_catalog(
            PROVIDER_MODELS_FALLBACK["openai"], openai_ids
        ),
        "deepseek": _merge_catalog(
            PROVIDER_MODELS_FALLBACK["deepseek"], deepseek_ids
        ),
    }
    source = {
        "openai": openai_src if openai_src == "api" else f"fallback:{openai_src}",
        "deepseek": deepseek_src if deepseek_src == "api" else f"fallback:{deepseek_src}",
    }
    _models_cache["ts"] = now
    _models_cache["models"] = models
    _models_cache["source"] = source
    return {"models": models, "source": source, "cached": False}


def resolved_llm_settings(
    overrides: Optional[Dict[str, Dict[str, str]]] = None,
    *,
    models: Optional[Dict[str, List[Dict[str, str]]]] = None,
    models_source: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    defaults = _env_defaults()
    ov = overrides or {}
    steps_out: List[Dict[str, Any]] = []
    current: Dict[str, Dict[str, str]] = {}
    catalog = models or PROVIDER_MODELS_FALLBACK
    for meta in LLM_STEPS:
        sid = meta["id"]
        pair = ov.get(sid) or defaults[sid]
        current[sid] = {
            "provider": pair["provider"],
            "model": pair["model"],
        }
        steps_out.append(
            {
                **meta,
                "provider": pair["provider"],
                "model": pair["model"],
                "default_provider": defaults[sid]["provider"],
                "default_model": defaults[sid]["model"],
                "customized": sid in ov,
            }
        )
    return {
        "steps": steps_out,
        "current": current,
        "providers": PROVIDERS,
        "models": catalog,
        "models_source": models_source
        or {"openai": "fallback", "deepseek": "fallback"},
    }


async def load_llm_settings(stor) -> Dict[str, Dict[str, str]]:
    from course.products import active_product_id

    raw = await stor.get_content_setting(active_product_id(), LLM_SETTINGS_KEY)
    return decode_llm_settings(raw)


async def load_step_model(stor, step: str) -> str:
    """Итоговая модель для шага: кастом из БД или дефолт из .env."""
    defaults = _env_defaults()
    sid = (step or "").strip()
    if sid not in defaults:
        raise ValueError(f"unknown llm step: {step}")
    ov = await load_llm_settings(stor)
    return str((ov.get(sid) or defaults[sid]).get("model") or defaults[sid]["model"])


async def save_llm_settings(
    stor, payload: Any, *, user_id: int = 0
) -> Dict[str, Any]:
    from course.products import active_product_id

    _ = user_id
    src = payload
    if isinstance(payload, dict) and isinstance(payload.get("steps"), dict):
        src = payload["steps"]
    elif isinstance(payload, dict) and "current" in payload:
        src = payload["current"]
    if not isinstance(src, dict):
        src = {}

    defaults = _env_defaults()
    live = await fetch_provider_models()
    catalog = live["models"]
    cleaned: Dict[str, Dict[str, str]] = {}
    for sid in ("plan", "distill", "write"):
        row = src.get(sid)
        if isinstance(row, str):
            model = row.strip()
            prov = provider_for_model(model) if model else defaults[sid]["provider"]
        elif isinstance(row, dict):
            model = str(row.get("model") or "").strip()
            prov = str(row.get("provider") or "").strip().lower()
        else:
            continue
        if not model:
            continue
        if prov not in ("openai", "deepseek"):
            prov = provider_for_model(model)
        if provider_for_model(model) != prov:
            first = (catalog.get(prov) or PROVIDER_MODELS_FALLBACK.get(prov) or [{}])[0]
            model = str(first.get("id") or model)
        pair = {"provider": prov, "model": model[:120]}
        if pair != defaults[sid]:
            cleaned[sid] = pair

    await stor.set_content_setting(active_product_id(), LLM_SETTINGS_KEY, cleaned)
    stored = decode_llm_settings(
        await stor.get_content_setting(active_product_id(), LLM_SETTINGS_KEY)
    )
    return resolved_llm_settings(
        stored, models=catalog, models_source=live.get("source")
    )


async def list_llm_settings(stor, *, refresh_models: bool = False) -> Dict[str, Any]:
    live = await fetch_provider_models(force=refresh_models)
    return resolved_llm_settings(
        await load_llm_settings(stor),
        models=live["models"],
        models_source=live.get("source"),
    )
