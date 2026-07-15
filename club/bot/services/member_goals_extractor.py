"""Извлечение stated_goals из реплик участника (только append, без перезаписи)."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI

from bot.services.agent_datetime_context import prepend_datetime_context
from bot.texts.prompts.member_goals_extract import GOALS_EXTRACT_SYSTEM
from config import config
from storage.db.llm_token_normalize import extract_token_counts_and_extras

logger = logging.getLogger(__name__)

CHAT_MODEL = "deepseek-chat"
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _strip_json_fence(raw: str) -> str:
    return _JSON_FENCE_RE.sub("", (raw or "").strip()).strip()


def _history_user_snippet(history: Optional[List[Dict[str, Any]]], *, max_msgs: int = 4) -> str:
    if not history:
        return ""
    tail = history[-max_msgs:]
    lines: list[str] = []
    for msg in tail:
        role = msg.get("role") or "?"
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        label = "Участник" if role == "user" else "Менеджер"
        lines.append(f"{label}: {content[:400]}")
    return "\n".join(lines)


async def _log_llm(user_storage, user_id: int, usage) -> None:
    request_id = str(uuid.uuid4())
    await user_storage.log_llm_completion_usage(
        user_id=user_id,
        provider="deepseek",
        model=CHAT_MODEL,
        usage=usage,
        request_kind="member_goals_extract",
        request_id=request_id,
    )
    pt, ct, tt, *_ = extract_token_counts_and_extras(usage)
    await user_storage.log_interaction(
        user_id=user_id,
        event_category="llm",
        event_type=f"deepseek_{CHAT_MODEL}_member_goals_extract",
        data={
            "request_id": request_id,
            "model": CHAT_MODEL,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": tt,
        },
        source="deepseek",
        outcome="success",
    )


async def extract_and_append_member_goals(
    *,
    user_storage,
    llm_client: AsyncOpenAI,
    user_id: int,
    user_message: str,
    history: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """
    Анализирует реплику участника и при явной цели дополняет stated_goals.

    Возвращает True, если профиль обновлён.
    """
    if not config.MEMBER_GOALS_EXTRACT_ENABLED:
        return False
    if not (user_message or "").strip():
        return False
    if not await user_storage.user_has_active_license(user_id):
        return False

    profile = await user_storage.get_member_profile(user_id)
    existing = (profile or {}).get("stated_goals") or ""
    history_snip = _history_user_snippet(history)

    user_block = (
        f"Уже известные цели (НЕ заменять, только дополнять новым):\n"
        f"{existing.strip() or '—'}\n\n"
        f"Последние реплики:\n{history_snip or '—'}\n\n"
        f"Новое сообщение участника:\n{(user_message or '').strip()[:1500]}"
    )

    try:
        resp = await llm_client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": prepend_datetime_context(GOALS_EXTRACT_SYSTEM)},
                {"role": "user", "content": user_block},
            ],
            temperature=0.1,
            max_tokens=300,
        )
        await _log_llm(user_storage, user_id, resp.usage)
        data = json.loads(_strip_json_fence(resp.choices[0].message.content or ""))
    except Exception as e:
        logger.warning("goals extract uid=%s: %s", user_id, e)
        return False

    action = str(data.get("action") or "none").strip().lower()
    if action != "append":
        return False

    fragment = str(data.get("fragment") or "").strip()
    if len(fragment) < 5:
        return False

    return await user_storage.append_member_stated_goals_fragment(
        user_id, fragment, source="llm_extract"
    )


async def extract_and_append_member_goals_safe(
    *,
    user_storage,
    llm_client: AsyncOpenAI,
    user_id: int,
    user_message: str,
    history: Optional[List[Dict[str, Any]]] = None,
) -> None:
    try:
        updated = await extract_and_append_member_goals(
            user_storage=user_storage,
            llm_client=llm_client,
            user_id=user_id,
            user_message=user_message,
            history=history,
        )
        if updated:
            logger.info("stated_goals appended uid=%s", user_id)
    except Exception as e:
        logger.warning("goals extract safe uid=%s: %s", user_id, e)
