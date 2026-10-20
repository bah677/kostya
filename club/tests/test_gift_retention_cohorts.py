"""Раскладка подарочной волны по тестовым когортам удержания.

Главное, что проверяем, — пропорциональность. Первая версия скрипта
раздавала квоты «кто попался первым», и все сорок человек приехали из одной
страты: когорты получались несопоставимыми, а разница в оплатах показывала
бы состав группы, а не вариант письма.
"""

from __future__ import annotations

import importlib.util
from datetime import date, datetime, timedelta
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "assign_gift_retention_cohorts",
    Path(__file__).resolve().parents[1] / "scripts" / "assign_gift_retention_cohorts.py",
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)

TODAY = date(2026, 10, 20)
SOON = date(2026, 10, 24)
LATER = date(2026, 10, 30)


def _person(uid: int, *, days_silent: int, msgs_group: int, expires_on: date):
    return {
        "user_id": uid,
        "expires_on": expires_on,
        "last_msg": datetime(TODAY.year, TODAY.month, TODAY.day)
        - timedelta(days=days_silent),
        "msgs_group": msgs_group,
        "in_group": True,
    }


def _cohort():
    """Состав, близкий к боевому: 26 живых, 9 пишут, 13 притихли, 53 ушли."""
    rows = []
    uid = 1000
    for n, kwargs in (
        (18, {"days_silent": 1, "msgs_group": 5, "expires_on": LATER}),
        (8, {"days_silent": 1, "msgs_group": 5, "expires_on": SOON}),
        (7, {"days_silent": 2, "msgs_group": 0, "expires_on": LATER}),
        (2, {"days_silent": 2, "msgs_group": 0, "expires_on": SOON}),
        (13, {"days_silent": 10, "msgs_group": 1, "expires_on": LATER}),
        (41, {"days_silent": 30, "msgs_group": 0, "expires_on": LATER}),
        (12, {"days_silent": 30, "msgs_group": 0, "expires_on": SOON}),
    ):
        for _ in range(n):
            uid += 1
            rows.append(_person(uid, **kwargs))
    return rows


def _plan(rows, seed="t", per_variant=10):
    return mod.plan_assignment(rows, seed=seed, per_variant=per_variant, today=TODAY)


def _sizes(plan):
    out = {}
    for p in plan.values():
        out[p["variant"]] = out.get(p["variant"], 0) + 1
    return out


def test_cohort_sizes_exact():
    sizes = _sizes(_plan(_cohort()))
    for v in mod.VARIANT_ORDER:
        assert sizes[v] == 10, f"{v}: {sizes[v]}"
    assert sizes[mod.VARIANT_CONTROL] == 101 - 40


def test_every_person_assigned_once():
    rows = _cohort()
    plan = _plan(rows)
    assert len(plan) == len(rows)
    assert set(plan) == {r["user_id"] for r in rows}


def test_no_variant_drawn_from_single_stratum():
    """Ни один вариант не набран из одной страты целиком."""
    plan = _plan(_cohort())
    for v in mod.VARIANT_ORDER:
        strata = {p["stratum"] for p in plan.values() if p["variant"] == v}
        assert len(strata) >= 3, f"{v} собран из {len(strata)} страт: {strata}"


def test_large_stratum_does_not_eat_all_quota():
    """Самая крупная страта даёт в каждый вариант долю, а не всё."""
    plan = _plan(_cohort())
    big = "ушли-молча|позже"  # 41 из 101 человека
    for v in mod.VARIANT_ORDER:
        from_big = sum(
            1 for p in plan.values() if p["variant"] == v and p["stratum"] == big
        )
        assert 2 <= from_big <= 6, f"{v}: из крупной страты {from_big} из 10"


def test_deterministic_for_same_seed():
    rows = _cohort()
    assert _plan(rows, seed="a") == _plan(rows, seed="a")
    assert _plan(rows, seed="a") != _plan(rows, seed="b")


def test_small_cohort_takes_everyone():
    """Если людей меньше, чем мест, контроля не остаётся."""
    plan = _plan(_cohort()[:12], per_variant=10)
    assert all(p["variant"] != mod.VARIANT_CONTROL for p in plan.values())


def test_segments():
    seg = mod._segment
    assert seg(_person(1, days_silent=1, msgs_group=3, expires_on=SOON), TODAY) == "живые"
    assert seg(_person(2, days_silent=1, msgs_group=0, expires_on=SOON), TODAY) == "пишут-боту"
    assert seg(_person(3, days_silent=10, msgs_group=0, expires_on=SOON), TODAY) == "притихли"
    assert seg(_person(4, days_silent=40, msgs_group=0, expires_on=SOON), TODAY) == "ушли-молча"
