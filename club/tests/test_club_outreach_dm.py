"""Тесты клубных рассылок в личку."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from bot.services.club_engagement_policy import (
    _adaptive_scripture_slot,
    _explicit_refusal,
)
from bot.services.club_scripture_dm import _parse_scripture_batch_json
from bot.utils.admin_outreach_skip import (
    should_skip_referral_bonus_dm,
    should_skip_subscription_outreach_slug,
)

MSK = ZoneInfo("Europe/Moscow")


def test_explicit_refusal_detects_stop():
    assert _explicit_refusal("Пожалуйста, не пишите мне так часто")
    assert not _explicit_refusal("Спасибо за напоминание")


def test_adaptive_scripture_active_user():
    profile = {
        "last_group_activity_at": datetime.now(MSK),
    }
    assert _adaptive_scripture_slot(profile, 0, slot_hour=7)
    assert not _adaptive_scripture_slot(profile, 0, slot_hour=21)


def test_parse_scripture_batch_json():
    raw = json.dumps(
        {
            "rationale": "В группе обсуждали поддержку друг друга",
            "quote_html": "<blockquote>Текст\n\n<i>(Мф. 7:7)</i></blockquote>",
        },
        ensure_ascii=False,
    )
    parsed = _parse_scripture_batch_json(raw)
    assert parsed is not None
    assert "поддержку" in parsed.rationale
    assert "<blockquote>" in parsed.quote_html


@pytest.mark.asyncio
async def test_admin_skip_referral_bonus():
    storage = AsyncMock()
    storage.is_telegram_admin_id = AsyncMock(return_value=True)
    assert await should_skip_referral_bonus_dm(storage, 123)


@pytest.mark.asyncio
async def test_refresh_pilot_includes_admins():
    from bot.services.club_outreach_pilot import refresh_pilot_cohort

    storage = AsyncMock()
    storage.fetch_top_group_active_user_ids = AsyncMock(return_value=[100, 200])
    storage.list_telegram_admin_ids = AsyncMock(
        return_value=[{"telegram_user_id": 304631563}, {"telegram_user_id": 100}]
    )
    storage.set_pilot_cohort = AsyncMock()

    ids = await refresh_pilot_cohort(storage)
    assert 304631563 in ids
    assert ids[0] == 304631563
    assert 100 in ids
    assert 200 in ids
    storage.set_pilot_cohort.assert_called_once()
    cohort_arg = storage.set_pilot_cohort.call_args[0][0]
    assert cohort_arg[0] == 304631563
    storage = AsyncMock()
    storage.is_telegram_admin_id = AsyncMock(return_value=True)
    assert await should_skip_subscription_outreach_slug(
        storage, 1, "bonus_extension_plus_one_day"
    )
    storage.is_telegram_admin_id = AsyncMock(return_value=False)
    assert not await should_skip_subscription_outreach_slug(
        storage, 1, "bonus_extension_plus_one_day"
    )
