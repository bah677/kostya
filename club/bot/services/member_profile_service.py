"""Сборка контекста профиля участника для member-агента."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")
_TME_LINK_RE = re.compile(r"https://t\.me/[^\s\]<>\")']+", re.IGNORECASE)

_ONBOARDING_LABELS = {
    "not_started": "ещё не начат",
    "started": "только вступил — проведи мягкий онбординг",
    "active": "уже общается",
}


def extract_tme_links_from_html(text: str) -> List[str]:
    if not text:
        return []
    return list(dict.fromkeys(m.rstrip(".,;:)") for m in _TME_LINK_RE.findall(text)))


def _fmt_dt_msk(dt: Any) -> str:
    if dt is None:
        return "—"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return dt[:16]
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=MSK)
        return dt.astimezone(MSK).strftime("%d.%m.%Y %H:%M")
    return str(dt)


def _json_list(val: Any) -> List[Any]:
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def build_member_profile_prompt_addon(profile: Optional[Dict[str, Any]]) -> str:
    """Краткий блок для system prompt member-агента."""
    if not profile:
        return ""

    stage = str(profile.get("onboarding_stage") or "not_started")
    stage_hint = _ONBOARDING_LABELS.get(stage, stage)
    goals = (profile.get("stated_goals") or "").strip()
    topics = _json_list(profile.get("topics_json"))
    materials = _json_list(profile.get("materials_sent_json"))

    lines = [
        "=== ПРОФИЛЬ УЧАСТНИКА (из БД, не выдумывай) ===",
        f"Онбординг: {stage_hint}",
    ]

    joined = profile.get("joined_at")
    if joined:
        lines.append(f"В клубе с: {_fmt_dt_msk(joined)}")

    expires = profile.get("license_expires_at")
    if expires:
        lines.append(f"Участие до: {_fmt_dt_msk(expires)}")
    elif profile.get("_license_expires_from_db"):
        lines.append(f"Участие до: {_fmt_dt_msk(profile['_license_expires_from_db'])}")

    if goals:
        lines.append(f"Что важно участнику (сам говорил): {goals[:500]}")

    if topics:
        recent = " | ".join(str(t)[:80] for t in topics[:5])
        lines.append(f"Недавние темы в личке: {recent}")

    if materials:
        sent_links = [
            str(m.get("link", ""))[:80]
            for m in materials[:5]
            if isinstance(m, dict) and m.get("link")
        ]
        if sent_links:
            lines.append(f"Уже рекомендовал материалы: {', '.join(sent_links)}")

    last_dm = profile.get("last_dm_at")
    if last_dm:
        lines.append(f"Последнее сообщение в личку: {_fmt_dt_msk(last_dm)}")

    last_grp = profile.get("last_group_activity_at")
    if last_grp:
        lines.append(f"Активность в группе: {_fmt_dt_msk(last_grp)}")

    if stage == "started":
        lines.append(
            "Задача сейчас: коротко поздравить (если уместно), показать что есть в клубе "
            "(эфиры, молитвы, чат), спросить что сейчас важнее всего — одним вопросом."
        )

    lines.append(
        "Не повторяй дословно одни и те же рекомендации, если уже давал ссылку выше."
    )
    return "\n".join(lines)


async def maybe_touch_member_group_activity(user_storage, user_id: int) -> None:
    """Обновить last_group_activity_at для участника с активной лицензией."""
    try:
        if not await user_storage.user_has_active_license(user_id):
            return
        await user_storage.touch_member_group_activity(user_id)
    except Exception as e:
        logger.debug("maybe_touch_member_group_activity uid=%s: %s", user_id, e)


async def prepare_member_dm_turn(
    user_storage,
    user_id: int,
    user_message: str,
) -> str:
    """Обновить профиль по входящему сообщению и вернуть addon для промпта."""
    try:
        if not await user_storage.user_has_active_license(user_id):
            return ""
        await user_storage.touch_member_dm(user_id, user_message=user_message)
        profile = await user_storage.get_member_profile(user_id) or {}
        lic = await user_storage.get_user_active_license(user_id)
        if lic and lic.get("expires_at"):
            profile = dict(profile)
            profile["_license_expires_from_db"] = lic["expires_at"]
            try:
                await user_storage.sync_member_license_expires(
                    user_id, lic["expires_at"]
                )
            except Exception:
                pass
        return build_member_profile_prompt_addon(profile)
    except Exception as e:
        logger.warning("prepare_member_dm_turn uid=%s: %s", user_id, e)
        return ""


async def after_member_agent_reply(
    user_storage,
    user_id: int,
    response_html: str,
) -> None:
    """Записать отправленные ссылки в профиль."""
    try:
        if not await user_storage.user_has_active_license(user_id):
            return
        links = extract_tme_links_from_html(response_html or "")
        if links:
            await user_storage.record_member_materials_sent(user_id, links)
    except Exception as e:
        logger.warning("after_member_agent_reply uid=%s: %s", user_id, e)
