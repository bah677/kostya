"""Тесты ритма первой недели и eligibility дайджеста."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bot.services.club_digest_eligibility import (
    decide_digest_dm_eligibility,
    is_digest_holdout,
)
from bot.services.club_first_week_steps import resolve_due_step

MSK = ZoneInfo("Europe/Moscow")


def _fw(**kwargs):
    base = {
        "step": 1,
        "msgs_group": 0,
        "started_at": datetime.now(MSK) - timedelta(hours=3),
        "done_at": None,
        "ended_at": None,
        "step4_variant": "air",
    }
    base.update(kwargs)
    return base


def test_holdout_is_stable_20pct():
    hits = sum(1 for i in range(1000) if is_digest_holdout(i))
    assert 150 <= hits <= 250


def test_step2_due_after_2h_in_window():
    # 14:00 MSK + 3h after join
    now = datetime.now(MSK).replace(hour=14, minute=0, second=0, microsecond=0)
    fw = _fw(started_at=now - timedelta(hours=3))
    plan = resolve_due_step(fw, now=now)
    assert plan is not None
    assert plan.step == 2
    assert plan.skip_reason is None


def test_step2_skip_if_already_wrote():
    now = datetime.now(MSK).replace(hour=14, minute=0, second=0, microsecond=0)
    fw = _fw(started_at=now - timedelta(hours=3), msgs_group=1)
    plan = resolve_due_step(fw, now=now)
    assert plan is not None
    assert plan.skip_reason == "already_wrote_group"


def test_step5_skip_when_done():
    now = datetime.now(MSK).replace(hour=14, minute=0, second=0, microsecond=0)
    fw = _fw(
        step=4,
        msgs_group=3,
        done_at=now,
        started_at=now - timedelta(days=5),
    )
    plan = resolve_due_step(fw, now=now)
    assert plan is not None
    assert plan.step == 5
    assert plan.skip_reason == "msgs_group_ge_3"


def test_digest_eligibility_first_week_awaits_step3():
    elig = decide_digest_dm_eligibility(
        user_id=1,
        profile={"last_group_activity_at": None},
        outreach_state={},
        first_week={"step": 1, "ended_at": None},
    )
    assert not elig.allow
    assert elig.reason == "first_week_awaits_step3"


def test_digest_eligibility_wrote_24h():
    now = datetime.now(MSK)
    elig = decide_digest_dm_eligibility(
        user_id=999001,  # likely not holdout-sensitive for this assert
        profile={"last_group_activity_at": now - timedelta(hours=2)},
        outreach_state={},
        first_week=None,
        now=now,
    )
    assert not elig.allow
    assert elig.reason == "wrote_group_last_24h"


def test_digest_eligibility_holdout_outside_week1():
    # Find a holdout id
    uid = next(i for i in range(1, 50) if is_digest_holdout(i))
    now = datetime.now(MSK)
    elig = decide_digest_dm_eligibility(
        user_id=uid,
        profile={"last_group_activity_at": now - timedelta(days=5)},
        outreach_state={},
        first_week=None,
        now=now,
    )
    assert not elig.allow
    assert elig.reason == "holdout"


def test_catchup_step_midweek():
    from bot.services.club_first_week_steps import catchup_completed_step

    now = datetime.now(MSK).replace(hour=14, minute=0, second=0, microsecond=0)
    started = now - timedelta(days=3, hours=2)
    assert catchup_completed_step(started, now=now) == 4
