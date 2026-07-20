"""Тесты строки «не был в клубе после исключения» в админ-уведомлении об оплате."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.services.payment_admin_notification import (
    _club_absence_after_kick_line,
    classify_subscription_payment,
)


class _FakeStorage:
    def __init__(
        self,
        *,
        exclusion_at=None,
        subscription_expired_at=None,
        previous_expires_at=None,
    ):
        self._exclusion_at = exclusion_at
        self._subscription_expired_at = subscription_expired_at
        self._previous_expires_at = previous_expires_at

    async def get_last_club_exclusion_before(self, user_id, before):
        if self._exclusion_at and self._exclusion_at < before:
            return self._exclusion_at
        return None

    async def get_last_subscription_expired_at(self, user_id, *, before=None):
        if self._subscription_expired_at and (
            before is None or self._subscription_expired_at < before
        ):
            return self._subscription_expired_at
        return None

    async def get_license_history_previous_expires_for_payment(
        self, user_id, payment_id
    ):
        return self._previous_expires_at


@pytest.mark.asyncio
async def test_absence_line_mixed_naive_and_aware_datetimes():
    from datetime import timezone as tz

    paid = datetime(2026, 7, 20, 11, 27, 2, tzinfo=tz.utc)
    storage = _FakeStorage(exclusion_at=datetime(2026, 7, 20, 9, 1, 52))
    line = await _club_absence_after_kick_line(
        storage,
        user_id=1892103568,
        payment_id=911,
        paid_at=paid,
    )
    assert line is not None
    assert "Не был в клубе после исключения" in line


@pytest.mark.asyncio
async def test_absence_line_from_exclusion_record():
    paid = datetime(2026, 6, 26, 21, 33)
    storage = _FakeStorage(exclusion_at=datetime(2026, 6, 10, 12, 0))
    line = await _club_absence_after_kick_line(
        storage,
        user_id=1,
        payment_id=740,
        paid_at=paid,
    )
    assert line is not None
    assert "16" in line or "шестнадцать" in line.lower() or "дней" in line
    assert "оценка" not in line.lower()


@pytest.mark.asyncio
async def test_absence_line_from_subscription_expired_fallback():
    paid = datetime(2026, 6, 26, 21, 33)
    storage = _FakeStorage(subscription_expired_at=datetime(2026, 6, 3, 9, 0))
    line = await _club_absence_after_kick_line(
        storage,
        user_id=1055536612,
        payment_id=740,
        paid_at=paid,
    )
    assert line is not None
    assert "23" in line or "дней" in line
    assert "оценка" in line.lower()


@pytest.mark.asyncio
async def test_absence_line_elena_like_scenario():
    """Как у Elena: нет club_member_exclusions, есть subscription_expired в history."""
    paid = datetime(2026, 6, 26, 21, 33, 14)
    storage = _FakeStorage(
        subscription_expired_at=datetime(2026, 6, 3, 9, 0, 8)
    )
    line = await _club_absence_after_kick_line(
        storage,
        user_id=1055536612,
        payment_id=740,
        paid_at=paid,
    )
    assert "Не был в клубе после исключения" in line


def test_classify_resume_for_lapsed_license():
    now = datetime(2026, 6, 26)
    paid = datetime(2026, 6, 26, 21, 33)
    prior = [
        MagicMock(
            payment_id=1,
            paid_at=datetime(2026, 4, 1),
            tariff_name="1 месяц",
            tariff_type="base",
            pay_touch_key=None,
            pay_ref_name=None,
        )
    ]
    kind = classify_subscription_payment(
        tariff_type="base",
        was_license_active=False,
        license_before=None,
        prior=prior,
        now=now,
        paid_at=paid,
    )
    assert kind.code == "resume"
    assert "ВОЗОБНОВЛЕНИЕ" in kind.title
