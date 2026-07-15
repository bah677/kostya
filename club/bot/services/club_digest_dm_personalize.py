"""Персонализация дайджеста клуба для лички."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from bot.services.llm_call_logger import logged_deepseek_chat
from bot.services.llm_request_kinds import CLUB_DIGEST_PERSONALIZE
from bot.services.member_profile_service import build_member_profile_prompt_addon
from bot.texts.prompts.club_outreach_policy import DIGEST_PERSONALIZE_SYSTEM
from bot.utils.telegram_html import sanitize_telegram_html
from config import config

logger = logging.getLogger(__name__)


def _format_dm_history(messages: List[Dict[str, Any]]) -> str:
    lines = []
    for m in messages[-10:]:
        role = "Участник" if m.get("role") == "user" else "Бот"
        text = (m.get("content") or "").strip()[:800]
        if text:
            lines.append(f"{role}: {text}")
    return "\n".join(lines) or "(нет недавней переписки)"


async def personalize_digest_for_user(
    user_storage,
    *,
    user_id: int,
    base_digest_html: str,
    api_key: str,
    first_name: Optional[str] = None,
) -> Optional[str]:
    profile = await user_storage.get_member_profile(user_id)
    profile_addon = build_member_profile_prompt_addon(profile)
    dm_history = await user_storage.user_recent_private_messages(user_id, limit=12)
    user_block = (
        f"Имя: {first_name or 'участник'}\n\n"
        f"{profile_addon}\n\n"
        f"Общий дайджест вчерашнего дня в клубе:\n{base_digest_html}\n\n"
        f"Недавняя переписка с ботом:\n{_format_dm_history(dm_history)}"
    )
    raw, _ = await logged_deepseek_chat(
        user_storage,
        user_id=user_id,
        request_kind=CLUB_DIGEST_PERSONALIZE,
        api_key=api_key,
        system=DIGEST_PERSONALIZE_SYSTEM,
        user=user_block,
        temperature=0.55,
        max_tokens=900,
        timeout_sec=90.0,
    )
    if not raw:
        return None
    safe = sanitize_telegram_html(raw.strip())
    return safe if len(safe) >= 40 else None
