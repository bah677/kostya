"""Единая точка LLM chat completion с записью в token_usage."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional, Tuple

import httpx

from bot.services.agent_datetime_context import prepend_datetime_context
from bot.services.deepseek_churn_analysis import DEEPSEEK_API_URL, DEFAULT_MODEL
from storage.db.llm_token_normalize import extract_token_counts_and_extras

logger = logging.getLogger(__name__)


async def logged_deepseek_chat(
    user_storage,
    *,
    user_id: int,
    request_kind: str,
    api_key: str,
    system: str,
    user: str,
    model: str = DEFAULT_MODEL,
    timeout_sec: float = 240.0,
    temperature: float = 0.35,
    max_tokens: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """
    DeepSeek chat/completions с логированием usage.

    Returns:
        (content_text | None, usage_dict | None)
    """
    payload: Dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": prepend_datetime_context(system)},
            {"role": "user", "content": user},
        ],
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            r = await client.post(DEEPSEEK_API_URL, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.warning("logged_deepseek_chat %s uid=%s: %s", request_kind, user_id, e)
        return None, None

    choices = data.get("choices") or []
    if not choices:
        return None, None

    content = (choices[0].get("message") or {}).get("content") or None
    usage_raw = data.get("usage")

    if user_storage and usage_raw:
        request_id = str(uuid.uuid4())
        try:
            await user_storage.log_llm_completion_usage(
                user_id=int(user_id or 0),
                provider="deepseek",
                model=model,
                usage=usage_raw,
                request_kind=request_kind,
                request_id=request_id,
                metadata=metadata,
            )
            pt, ct, tt, *_ = extract_token_counts_and_extras(usage_raw)
            await user_storage.log_interaction(
                user_id=int(user_id or 0),
                event_category="llm",
                event_type=f"deepseek_{model}_{request_kind}",
                data={
                    "provider": "deepseek",
                    "request_id": request_id,
                    "model": model,
                    "request_kind": request_kind,
                    "prompt_tokens": pt,
                    "completion_tokens": ct,
                    "total_tokens": tt,
                },
                source="deepseek",
                outcome="success",
            )
        except Exception as e:
            logger.warning("logged_deepseek_chat log failed: %s", e)

    usage_dict = usage_raw if isinstance(usage_raw, dict) else None
    text = (content or "").strip() or None
    return text, usage_dict
