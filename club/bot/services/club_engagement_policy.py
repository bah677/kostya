"""Политика клубных проактивных рассылок в личку."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from bot.services.llm_call_logger import logged_deepseek_chat
from bot.services.llm_request_kinds import CLUB_OUTREACH_POLICY
from bot.texts.prompts.club_outreach_policy import OUTREACH_POLICY_SYSTEM
from config import config

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

_EXPLICIT_REFUSAL = re.compile(
    r"(?i)(?:^|\s)(?:стоп|хватит|не\s+пиш(?:и|ите)|отстан(?:ь|ьте)|"
    r"не\s+мешай|не\s+надо\s+рассыл|отпис|не\s+интерес).*(?:$|\s|!|\.)"
    r"|(?:не\s+присылай|задолбал|спам)"
)


@dataclass(frozen=True)
class OutreachDecision:
    allow: bool
    reason: str
    scripture_this_slot: bool = True


def _explicit_refusal(text: str) -> bool:
    return bool(_EXPLICIT_REFUSAL.search(text or ""))


def _recent_user_texts(messages: list[Dict[str, Any]]) -> str:
    lines = []
    for m in messages:
        if (m.get("role") or "") != "user":
            continue
        t = (m.get("content") or "").strip()
        if t:
            lines.append(t[:500])
    return "\n---\n".join(lines[-5:])


async def decide_club_outreach(
    user_storage,
    user_id: int,
    *,
    kind: str,
    slot_hour: Optional[int] = None,
    api_key: str = "",
) -> OutreachDecision:
    """
    kind: digest | scripture
    """
    if config.CLUB_OUTREACH_DM_PILOT_ONLY:
        pilot_ids = await user_storage.list_pilot_outreach_user_ids()
        if user_id not in pilot_ids:
            return OutreachDecision(False, "not_in_pilot")

    is_admin = await user_storage.is_telegram_admin_id(user_id)
    if not is_admin and not await user_storage.user_has_active_license(user_id):
        return OutreachDecision(False, "no_active_license")

    state = await user_storage.get_outreach_state(user_id) or {}
    paused = state.get("outreach_paused_until")
    if paused and isinstance(paused, datetime):
        if paused.tzinfo is None:
            paused = paused.replace(tzinfo=MSK)
        if paused > datetime.now(MSK):
            return OutreachDecision(False, "paused")

    sent_today = await user_storage.get_proactive_sent_count_today(user_id)
    if sent_today >= config.CLUB_OUTREACH_DAILY_LIMIT:
        return OutreachDecision(False, f"daily_limit_{sent_today}")

    recent = await user_storage.user_recent_private_messages(user_id, limit=15)
    user_blob = _recent_user_texts(recent)
    for line in user_blob.split("\n---\n"):
        if _explicit_refusal(line):
            until = datetime.now(MSK) + timedelta(days=30)
            await user_storage.set_outreach_paused(
                user_id, until, bump_complaint=True
            )
            return OutreachDecision(False, "explicit_refusal")

    profile = await user_storage.get_member_profile(user_id)
    suppression = int(state.get("suppression_level") or 0)

    if kind == "scripture":
        scripture_ok = _adaptive_scripture_slot(
            profile, suppression, slot_hour=slot_hour
        )
        if not scripture_ok:
            return OutreachDecision(False, "adaptive_skip_scripture", scripture_this_slot=False)

    key = (api_key or config.DEEPSEEK_API_KEY or "").strip()
    if not key:
        return OutreachDecision(True, "no_llm_policy_default_allow")

    user_block = (
        f"Тип рассылки: {kind}\n"
        f"Слот (час МСК): {slot_hour if slot_hour is not None else '—'}\n"
        f"Уже отправлено проактивных сегодня: {sent_today}\n"
        f"Suppression level: {suppression}\n"
        f"Последняя активность в группе: {profile.get('last_group_activity_at') if profile else '—'}\n"
        f"Последняя активность в личке: {profile.get('last_dm_at') if profile else '—'}\n\n"
        f"Недавние реплики пользователя в личке:\n{user_blob or '(нет)'}"
    )
    raw, _ = await logged_deepseek_chat(
        user_storage,
        user_id=user_id,
        request_kind=CLUB_OUTREACH_POLICY,
        api_key=key,
        system=OUTREACH_POLICY_SYSTEM,
        user=user_block,
        temperature=0.2,
        max_tokens=200,
        timeout_sec=45.0,
    )
    if not raw:
        return OutreachDecision(True, "policy_llm_fallback_allow")

    try:
        data = json.loads(raw.strip().strip("`").replace("```json", "").replace("```", ""))
        allow = bool(data.get("allow", True))
        reason = str(data.get("reason") or "")
        if not allow and data.get("pause_days"):
            days = min(60, max(1, int(data["pause_days"])))
            await user_storage.set_outreach_paused(
                user_id,
                datetime.now(MSK) + timedelta(days=days),
                bump_complaint=bool(data.get("complaint")),
            )
        return OutreachDecision(allow, reason or ("allow" if allow else "deny"))
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.debug("policy json parse uid=%s: %s raw=%s", user_id, e, raw[:200])
        if _explicit_refusal(raw):
            return OutreachDecision(False, "policy_text_refusal")
        return OutreachDecision(True, "policy_parse_fallback")


def _adaptive_scripture_slot(
    profile: Optional[Dict[str, Any]],
    suppression: int,
    *,
    slot_hour: Optional[int],
) -> bool:
    """Адаптивная частота цитат по активности."""
    if suppression >= 3:
        return slot_hour in (12, 21)
    if suppression >= 1:
        return slot_hour in (9, 15, 21)

    last_group = profile.get("last_group_activity_at") if profile else None
    last_dm = profile.get("last_dm_at") if profile else None
    now = datetime.now(MSK)
    active_recently = False
    for ts in (last_group, last_dm):
        if isinstance(ts, datetime):
            t = ts if ts.tzinfo else ts.replace(tzinfo=MSK)
            if (now - t.astimezone(MSK)).days <= 3:
                active_recently = True
                break

    if active_recently:
        return slot_hour in (7, 12, 18)
    return slot_hour in (12,)
