"""Тесты пилотной группы outreach."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bot.services.club_outreach_pilot import refresh_pilot_cohort


@pytest.mark.asyncio
async def test_refresh_pilot_includes_admins_first():
    storage = AsyncMock()
    storage.fetch_top_group_active_user_ids = AsyncMock(return_value=[100, 200, 300])
    storage.list_telegram_admin_ids = AsyncMock(
        return_value=[{"telegram_user_id": 999}, {"telegram_user_id": 200}]
    )
    storage.set_pilot_cohort = AsyncMock()

    result = await refresh_pilot_cohort(storage)

    assert result[0] == 999
    assert 200 in result
    assert 100 in result
    storage.set_pilot_cohort.assert_called_once()
    cohort = storage.set_pilot_cohort.call_args[0][0]
    assert cohort[0] == 999
