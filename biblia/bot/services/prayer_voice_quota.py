# bot/services/prayer_voice_quota.py
"""Сутки бесплатных голосовых молитв: окно с 08:00 Europe/Moscow."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

_MSK = ZoneInfo("Europe/Moscow")
QUOTA_RESET_HOUR = 8
SETTING_KEY_DAILY_LIMIT = "prayer_voice_daily_limit"
DEFAULT_DAILY_LIMIT = 50
PAYMENT_PURPOSE_VOICE_UNLOCK = "prayer_voice_unlock"


def msk_now() -> datetime:
    return datetime.now(_MSK)


def quota_day_for(now: datetime | None = None) -> date:
    """Идентификатор «квотных суток»: до 08:00 МСК относится к вчерашнему дню."""
    now = now or msk_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=_MSK)
    else:
        now = now.astimezone(_MSK)
    if now.hour < QUOTA_RESET_HOUR:
        return (now - timedelta(days=1)).date()
    return now.date()


def quota_window(day: date | None = None) -> tuple[datetime, datetime]:
    """[start, end) для quota_day: 08:00 → 08:00 следующего дня."""
    day = day or quota_day_for()
    start = datetime.combine(day, time(QUOTA_RESET_HOUR, 0), tzinfo=_MSK)
    return start, start + timedelta(days=1)


def format_reset_hint(now: datetime | None = None) -> str:
    now = now or msk_now()
    _start, end = quota_window(quota_day_for(now))
    return end.strftime("%H:%M %d.%m.%Y")
