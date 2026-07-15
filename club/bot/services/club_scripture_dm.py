"""Цитаты Писания для лички: batch + персонализация."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from bot.services.club_daily_digest import _format_messages_blob
from bot.services.club_scripture_pulse import (
    ScripturePulseResult,
    fetch_club_group_messages_since,
    is_duplicate_pulse_quote,
    load_recent_pulse_quote_refs,
    load_recent_pulse_plain_texts,
    resolve_pulse_since,
    load_last_pulse_at,
    append_recent_pulse_quote,
    pulse_state_path,
    DEFAULT_PULSE_HOURS,
)
from bot.services.llm_call_logger import logged_deepseek_chat
from bot.services.llm_request_kinds import CLUB_SCRIPTURE_BASE, CLUB_SCRIPTURE_PERSONALIZE
from bot.services.member_profile_service import build_member_profile_prompt_addon
from bot.texts.prompts.club_outreach_policy import (
    SCRIPTURE_BATCH_SYSTEM,
    SCRIPTURE_PERSONALIZE_SYSTEM,
)
from bot.texts.prompts.club_scripture_pulse import format_recent_pulse_quotes_user_block
from bot.utils.telegram_html import sanitize_telegram_html
from config import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScriptureBatchResult:
    rationale: str
    quote_html: str
    message_count: int
    skipped: bool
    skip_reason: str = ""


def _parse_scripture_batch_json(raw: str) -> Optional[ScriptureBatchResult]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    rationale = str(data.get("rationale") or "").strip()
    quote = str(data.get("quote_html") or "").strip()
    if not rationale or not quote or "<blockquote" not in quote.lower():
        return None
    return ScriptureBatchResult(
        rationale=rationale,
        quote_html=sanitize_telegram_html(quote),
        message_count=0,
        skipped=False,
    )


async def build_scripture_batch(
    pool,
    user_storage,
    *,
    club_group_id: int,
    api_key: str,
    slot_hour: int,
    digest_topic_id: int = 0,
) -> ScriptureBatchResult:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    MSK = ZoneInfo("Europe/Moscow")
    now_msk = datetime.now(MSK)
    last = load_last_pulse_at()
    since = resolve_pulse_since(
        last_run=last,
        now_msk=now_msk,
        slot_hour=slot_hour,
        pulse_hours=DEFAULT_PULSE_HOURS,
    )
    rows = await fetch_club_group_messages_since(
        pool,
        club_group_id=club_group_id,
        since=since,
        exclude_topic_id=digest_topic_id,
    )
    min_msg = config.CLUB_SCRIPTURE_PULSE_MIN_MESSAGES
    if len(rows) < min_msg:
        return ScriptureBatchResult(
            rationale="",
            quote_html="",
            message_count=len(rows),
            skipped=True,
            skip_reason=f"few_messages_{len(rows)}",
        )

    blob, _ = _format_messages_blob(rows)
    recent_refs = load_recent_pulse_quote_refs()
    recent_plain = load_recent_pulse_plain_texts()
    user = (
        f"<<<ПЕРЕПИСКА>>>\n{blob[:80_000]}\n<<<КОНЕЦ>>>"
        f"{format_recent_pulse_quotes_user_block(recent_refs)}"
    )

    for attempt in range(4):
        raw, _ = await logged_deepseek_chat(
            user_storage,
            user_id=0,
            request_kind=CLUB_SCRIPTURE_BASE,
            api_key=api_key,
            system=SCRIPTURE_BATCH_SYSTEM,
            user=user + (f"\n\n⚠️ Попытка {attempt + 1}: другой стих." if attempt else ""),
            temperature=0.55 + attempt * 0.08,
            max_tokens=400,
            timeout_sec=120.0,
        )
        if not raw:
            continue
        parsed = _parse_scripture_batch_json(raw)
        if not parsed:
            continue
        if is_duplicate_pulse_quote(parsed.quote_html, recent_refs, recent_plain=recent_plain):
            continue
        return ScriptureBatchResult(
            rationale=parsed.rationale,
            quote_html=parsed.quote_html,
            message_count=len(rows),
            skipped=False,
        )

    return ScriptureBatchResult(
        rationale="",
        quote_html="",
        message_count=len(rows),
        skipped=True,
        skip_reason="llm_failed",
    )


def _format_dm_history(messages: List[Dict[str, Any]]) -> str:
    lines = []
    for m in messages[-10:]:
        role = "Участник" if m.get("role") == "user" else "Бот"
        text = (m.get("content") or "").strip()[:600]
        if text:
            lines.append(f"{role}: {text}")
    return "\n".join(lines) or "(нет)"


async def personalize_scripture_for_user(
    user_storage,
    *,
    user_id: int,
    batch: ScriptureBatchResult,
    api_key: str,
    first_name: Optional[str] = None,
) -> Optional[str]:
    profile = await user_storage.get_member_profile(user_id)
    profile_addon = build_member_profile_prompt_addon(profile)
    dm_history = await user_storage.user_recent_private_messages(user_id, limit=12)
    user_block = (
        f"Имя: {first_name or 'участник'}\n\n"
        f"{profile_addon}\n\n"
        f"Почему выбран стих (batch): {batch.rationale}\n\n"
        f"Стих (HTML):\n{batch.quote_html}\n\n"
        f"Переписка с ботом:\n{_format_dm_history(dm_history)}"
    )
    raw, _ = await logged_deepseek_chat(
        user_storage,
        user_id=user_id,
        request_kind=CLUB_SCRIPTURE_PERSONALIZE,
        api_key=api_key,
        system=SCRIPTURE_PERSONALIZE_SYSTEM,
        user=user_block,
        temperature=0.55,
        max_tokens=700,
        timeout_sec=90.0,
    )
    if not raw:
        return None
    safe = sanitize_telegram_html(raw.strip())
    if "<blockquote" not in safe.lower():
        safe = (
            f"{safe}\n\n<i>{sanitize_telegram_html(batch.rationale)}</i>\n\n"
            f"{batch.quote_html}"
        )
    return safe if len(safe) >= 30 else None


def commit_scripture_batch(batch: ScriptureBatchResult) -> None:
    from bot.services.club_scripture_pulse import save_last_pulse_at

    if batch.quote_html:
        append_recent_pulse_quote(
            f"<i>{batch.rationale}</i>\n{batch.quote_html}",
            pulse_state_path(),
        )
        from datetime import datetime, timezone

        from bot.services.club_scripture_pulse import MSK

        save_last_pulse_at(datetime.now(timezone.utc).astimezone(MSK))
