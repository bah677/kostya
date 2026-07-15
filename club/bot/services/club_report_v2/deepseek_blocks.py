"""DeepSeek-блоки для ежедневного отчёта v2."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from bot.services.deepseek_churn_analysis import (
    DEEPSEEK_API_URL,
    DEFAULT_MODEL,
)
from bot.texts.prompts.report_daily import (
    GROUP_DAY_SYSTEM,
    LEAD_DIALOGS_SYSTEM,
    group_day_user_content,
    lead_dialogs_user_content,
)

import httpx

logger = logging.getLogger(__name__)


async def _chat(
    *,
    api_key: str,
    system: str,
    user: str,
    model: str = DEFAULT_MODEL,
    timeout_sec: float = 240.0,
    temperature: float = 0.35,
    max_tokens: Optional[int] = None,
) -> Optional[str]:
    payload: Dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
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
        choices = data.get("choices") or []
        if not choices:
            return None
        return (choices[0].get("message") or {}).get("content") or None
    except Exception as e:
        logger.warning("daily report deepseek failed: %s", e)
        return None


async def analyze_group_day(
    *,
    api_key: str,
    messages_blob: str,
    stats_line: str,
) -> Optional[str]:
    user = group_day_user_content(stats_line=stats_line, messages_blob=messages_blob)
    return await _chat(
        api_key=api_key, system=GROUP_DAY_SYSTEM, user=user, timeout_sec=300.0
    )


async def analyze_lead_dialogs(
    *,
    api_key: str,
    dialogs_blob: str,
    aggregates: Dict[str, Any],
    paid_brief_blob: str,
) -> Optional[str]:
    user = lead_dialogs_user_content(
        aggregates_json=json.dumps(aggregates, ensure_ascii=False, default=str)[:20_000],
        dialogs_blob=dialogs_blob,
        paid_brief_blob=paid_brief_blob,
    )
    return await _chat(
        api_key=api_key, system=LEAD_DIALOGS_SYSTEM, user=user, timeout_sec=360.0
    )


def format_dialogs_for_llm(
    dialogs: List[Dict[str, Any]],
    *,
    max_messages: int = 20,
) -> str:
    parts: List[str] = []
    for d in dialogs:
        uid = d.get("user_id")
        lines = d.get("messages") or []
        tail = lines[-max_messages:] if len(lines) > max_messages else lines
        parts.append(f"--- user_id={uid} ---")
        for m in tail:
            role = m.get("role", "?")
            text = (m.get("content") or "")[:2000]
            parts.append(f"{role}: {text}")
        parts.append("")
    return "\n".join(parts)
