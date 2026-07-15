"""Тесты трекинга активности участника в группе."""

import pytest

from bot.services.member_profile_service import maybe_touch_member_group_activity


@pytest.mark.asyncio
async def test_touch_skips_without_license(fake_storage):
    fake_storage.licenses[1] = False
    await maybe_touch_member_group_activity(fake_storage, 1)
    assert 1 not in fake_storage.profiles


@pytest.mark.asyncio
async def test_touch_updates_active_member(fake_storage):
    fake_storage.licenses[2] = True
    await maybe_touch_member_group_activity(fake_storage, 2)
    assert fake_storage.profiles[2]["last_group_activity_at"] == "touched"
