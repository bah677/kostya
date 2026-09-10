"""Тесты веток продления (ПРО-1…3)."""

from bot.services.renewal_branch import (
    group_life_branch,
    normalize_license_origin,
    reminder_keyboard_for_branch,
)
from bot.texts import ru_subscription_reminder as sub_txt


def test_group_life_branches():
    assert group_life_branch(0) == "silent"
    assert group_life_branch(1) == "peeking"
    assert group_life_branch(9) == "peeking"
    assert group_life_branch(10) == "living"


def test_normalize_origin():
    assert normalize_license_origin("gift") == "gift"
    assert normalize_license_origin("trial") == "trial"
    assert normalize_license_origin(None, license_type="admin_grant") == "gift"
    assert normalize_license_origin("payment") == "payment"


def test_silent_keeps_payment_keyboard():
    assert reminder_keyboard_for_branch("silent") == "payment_extend"
    assert reminder_keyboard_for_branch("living") == "payment_extend"


def test_reminder_schedules_three_each():
    by = sub_txt.REMINDER_BY_ORIGIN
    assert len(by["payment"]) == 3
    assert len(by["gift"]) == 3
    assert len(by["trial"]) == 3
    assert [r["days_before"] for r in by["payment"]] == [7, 3, 1]
    assert [r["days_before"] for r in by["gift"]] == [10, 5, 1]
    assert [r["days_before"] for r in by["trial"]] == [6, 3, 0]


def test_churn_only_plus_5():
    assert len(sub_txt.CHURN_MESSAGES) == 1
    assert sub_txt.CHURN_MESSAGES[0]["slug"] == "churn_plus_5d"
