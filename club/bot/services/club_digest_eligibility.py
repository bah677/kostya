"""Правила получателей дайджеста в личку (НЕД-3)."""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")

# Порог «пустого дня» для DM-дайджеста (НЕД-3 п.4).
DIGEST_DM_MIN_HUMAN_MESSAGES = 10


def is_digest_holdout(user_id: int) -> bool:
    """20% holdout: crc32(user_id) % 5 == 0. Новички первой недели не сюда."""
    return (zlib.crc32(str(int(user_id)).encode("utf-8")) & 0xFFFFFFFF) % 5 == 0


def _as_aware(ts: Optional[datetime]) -> Optional[datetime]:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=MSK)
    return ts.astimezone(MSK)


@dataclass(frozen=True)
class DigestEligibility:
    allow: bool
    reason: str


def decide_digest_dm_eligibility(
    *,
    user_id: int,
    profile: Optional[Dict[str, Any]],
    outreach_state: Optional[Dict[str, Any]],
    first_week: Optional[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> DigestEligibility:
    """
    Кому слать дайджест из outreach DM.

    - Первая неделя открыта и step < 3 → не слать (шаг 3 сделает proactive).
    - Первая неделя, step >= 3 → можно по общим правилам, без holdout пока неделя открыта.
    - Иначе: молчал в группе > 3 дней; не писал в группу за 24ч;
      не чаще раза в 2 суток; holdout 20%.
    """
    now = now or datetime.now(MSK)
    fw = first_week or {}
    fw_open = bool(fw) and fw.get("ended_at") is None
    fw_step = int(fw.get("step") or 0) if fw else 0

    if fw_open and fw_step < 3:
        return DigestEligibility(False, "first_week_awaits_step3")

    last_group = _as_aware((profile or {}).get("last_group_activity_at"))
    if last_group and (now - last_group) < timedelta(hours=24):
        return DigestEligibility(False, "wrote_group_last_24h")

    # Вне первой недели (или после step3): нужны «молчаливые» > 3 дней
    if not fw_open:
        if last_group and (now - last_group) < timedelta(days=3):
            return DigestEligibility(False, "group_active_within_3d")
        if is_digest_holdout(user_id):
            return DigestEligibility(False, "holdout")

    last_digest = _as_aware((outreach_state or {}).get("last_digest_dm_at"))
    if last_digest and (now - last_digest) < timedelta(days=2):
        return DigestEligibility(False, "digest_cooldown_2d")

    return DigestEligibility(True, "ok")
