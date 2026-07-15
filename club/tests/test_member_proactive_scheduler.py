"""Тесты планировщика member_proactive."""

from apscheduler.triggers.cron import CronTrigger

from bot.features.member_proactive import (
    _parse_proactive_hours,
    _proactive_hours_cron_expr,
)


def test_parse_proactive_hours():
    assert _parse_proactive_hours("9,12,15,18,21") == [9, 12, 15, 18, 21]
    assert _parse_proactive_hours("") == [9, 12, 15, 18, 21]


def test_cron_trigger_accepts_proactive_hours():
    expr = _proactive_hours_cron_expr("9,12,15,18,21")
    assert expr == "9,12,15,18,21"
    CronTrigger(hour=expr, minute=30, timezone="Europe/Moscow")
