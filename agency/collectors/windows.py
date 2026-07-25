"""MSK day windows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, time
from typing import Optional
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")


@dataclass(frozen=True)
class DayWindow:
    day: date
    start_utc: datetime
    end_utc: datetime

    @property
    def mau_start_utc(self) -> datetime:
        return self.end_utc - timedelta(days=30)


def day_window(day: date) -> DayWindow:
    """[day 00:00 MSK, next day 00:00 MSK) as UTC-aware datetimes."""
    start_local = datetime.combine(day, time.min, tzinfo=MSK)
    end_local = start_local + timedelta(days=1)
    return DayWindow(
        day=day,
        start_utc=start_local.astimezone(ZoneInfo("UTC")),
        end_utc=end_local.astimezone(ZoneInfo("UTC")),
    )


def yesterday_msk(now: Optional[datetime] = None) -> date:
    now = now or datetime.now(MSK)
    if now.tzinfo is None:
        now = now.replace(tzinfo=MSK)
    else:
        now = now.astimezone(MSK)
    return (now - timedelta(days=1)).date()
