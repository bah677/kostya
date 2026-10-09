"""Слоты премьер YouTube — в часовом поясе аудитории канала.

Часы задаются числами (7, 12, 19), а в каком поясе их понимать — зависит от
языка: русский канал живёт по Москве, испанский по Мехико. Зритель смотрит
по своим часам, и «утренняя молитва по дороге» должна выходить утром у него,
а не у нас.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Sequence
from zoneinfo import ZoneInfo

_MSK = ZoneInfo("Europe/Moscow")
_DEFAULT_TZ = "Europe/Moscow"
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
    tz: str = _DEFAULT_TZ,
) -> datetime:
    """
    index 1-based → слот в поясе `tz` в день `day`.
    Если слот уже прошёл — тот же час на следующий день.
    Возвращает datetime с таймзоной; YouTube всё равно получит UTC.
    """
    if index < 1:
        raise ValueError("index must be >= 1")
    hours = list(slots_msk or _DEFAULT_SLOTS_MSK)
    if not hours:
        hours = list(_DEFAULT_SLOTS_MSK)
    hour = hours[(index - 1) % len(hours)]

    zone = ZoneInfo(tz or _DEFAULT_TZ)
    base_day = datetime.strptime(day, "%Y-%m-%d").date()
    now_local = (now or datetime.now(zone)).astimezone(zone)
    candidate = datetime(
        base_day.year,
        base_day.month,
        base_day.day,
        hour,
        0,
        0,
        tzinfo=zone,
    )
    min_ok = now_local + timedelta(minutes=_MIN_LEAD_MINUTES)
    while candidate < min_ok:
        candidate = candidate + timedelta(days=1)
    return candidate


def premiere_slot_label(dt: datetime, *, tz: str = _DEFAULT_TZ) -> str:
    """Подпись слота: местное время канала, а при чужом поясе — и МСК рядом."""
    zone = ZoneInfo(tz or _DEFAULT_TZ)
    local = dt.astimezone(zone)
    if zone.key == "Europe/Moscow":
        return local.strftime("%d.%m.%Y %H:%M МСК")
    msk = dt.astimezone(_MSK)
    short = zone.key.split("/")[-1].replace("_", " ")
    return (
        f"{local.strftime('%d.%m.%Y %H:%M')} {short}"
        f" ({msk.strftime('%H:%M')} МСК)"
    )


def to_youtube_publish_at(dt_msk: datetime) -> str:
    """RFC 3339 UTC для YouTube API ``status.publishAt``."""
    utc = dt_msk.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
