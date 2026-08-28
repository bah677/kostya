"""Слоты премьер YouTube: 09:00, 15:00, 21:00 МСК."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Sequence
from zoneinfo import ZoneInfo

_MSK = ZoneInfo("Europe/Moscow")
_DEFAULT_SLOTS_MSK = (9, 15, 21)
_MIN_LEAD_MINUTES = 20


def parse_premiere_hours(raw: str | Sequence[int] | None) -> List[int]:
    if isinstance(raw, (list, tuple)):
        hours = [int(h) % 24 for h in raw if h is not None]
        return sorted(set(hours)) or list(_DEFAULT_SLOTS_MSK)
    text = (raw or "").strip()
    if not text:
        return list(_DEFAULT_SLOTS_MSK)
    out: List[int] = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part) % 24)
        except ValueError:
            continue
    return sorted(set(out)) or list(_DEFAULT_SLOTS_MSK)


def premiere_slot_datetime(
    *,
    day: str,
    index: int,
    slots_msk: Sequence[int] | None = None,
    now: datetime | None = None,
) -> datetime:
    """
    index 1-based → слоты 9 / 15 / 21 МСК в день `day`.
    Если слот уже прошёл — тот же час на следующий день.
    """
    if index < 1:
        raise ValueError("index must be >= 1")
    hours = list(slots_msk or _DEFAULT_SLOTS_MSK)
    if not hours:
        hours = list(_DEFAULT_SLOTS_MSK)
    hour = hours[(index - 1) % len(hours)]

    base_day = datetime.strptime(day, "%Y-%m-%d").date()
    now_msk = (now or datetime.now(_MSK)).astimezone(_MSK)
    candidate = datetime(
        base_day.year,
        base_day.month,
        base_day.day,
        hour,
        0,
        0,
        tzinfo=_MSK,
    )
    min_ok = now_msk + timedelta(minutes=_MIN_LEAD_MINUTES)
    while candidate < min_ok:
        candidate = candidate + timedelta(days=1)
    return candidate


def premiere_slot_label(dt_msk: datetime) -> str:
    local = dt_msk.astimezone(_MSK)
    return local.strftime("%d.%m.%Y %H:%M МСК")


def to_youtube_publish_at(dt_msk: datetime) -> str:
    """RFC 3339 UTC для YouTube API ``status.publishAt``."""
    utc = dt_msk.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
