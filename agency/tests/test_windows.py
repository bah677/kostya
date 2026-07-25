"""Smoke tests without DB."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from collectors.windows import day_window, yesterday_msk
from llm.panel import parse_json_obj


def test_day_window_msk():
    w = day_window(date(2026, 7, 25))
    assert w.day.isoformat() == "2026-07-25"
    assert w.start_utc < w.end_utc
    assert (w.end_utc - w.start_utc) == timedelta(days=1)
    # 00:00 MSK = 21:00 UTC prev day in summer (MSK+3)
    assert w.start_utc.tzinfo is not None


def test_yesterday():
    now = datetime(2026, 7, 26, 1, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    assert yesterday_msk(now) == date(2026, 7, 25)


def test_parse_json():
    assert parse_json_obj('{"a": 1}')["a"] == 1
    assert parse_json_obj("```json\n{\"a\": 2}\n```")["a"] == 2
    assert parse_json_obj("nope") == {}
