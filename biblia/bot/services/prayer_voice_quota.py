# bot/services/prayer_voice_quota.py
"""Сутки бесплатных голосовых молитв: окно с 08:00 Europe/Moscow + формула от донатов."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

_MSK = ZoneInfo("Europe/Moscow")
QUOTA_RESET_HOUR = 8

# Минимум глобального пула из /adm (пол), если сборы дали меньше.
SETTING_KEY_DAILY_LIMIT = "prayer_voice_daily_limit"
SETTING_KEY_MIN_LIMIT = SETTING_KEY_DAILY_LIMIT
DEFAULT_MIN_LIMIT = 5
DEFAULT_DAILY_LIMIT = DEFAULT_MIN_LIMIT  # alias для совместимости

# Сколько бесплатных голосов одному человеку за квотные сутки (08:00 МСК).
SETTING_KEY_PER_USER_DAILY = "prayer_voice_per_user_daily"
DEFAULT_PER_USER_DAILY = 2

# Внутренние константы — не показывать пользователям.
COST_PER_PRAYER_USD = 0.2
DONATION_SHARE_FOR_VOICE = 0.35

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


def previous_quota_day(day: date | None = None) -> date:
    day = day or quota_day_for()
    return day - timedelta(days=1)


def format_reset_hint(now: datetime | None = None) -> str:
    now = now or msk_now()
    _start, end = quota_window(quota_day_for(now))
    return end.strftime("%H:%M %d.%m.%Y")


def slots_from_donation_usd(usd: float) -> int:
    """Сколько бесплатных слотов даёт сумма донатов в USD (доля × / себестоимость)."""
    if usd <= 0:
        return 0
    return int(math.floor((float(usd) * DONATION_SHARE_FOR_VOICE) / COST_PER_PRAYER_USD))


def apply_min_floor(computed_slots: int, min_floor: int) -> int:
    return max(int(min_floor or 0), int(computed_slots or 0))
