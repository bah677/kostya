"""Тесты блока даты/времени для LLM-агентов."""

from datetime import datetime
from zoneinfo import ZoneInfo

from bot.services.agent_datetime_context import (
    format_datetime_context,
    prepend_datetime_context,
)

MSK = ZoneInfo("Europe/Moscow")


def test_format_datetime_context_includes_msk():
    fixed = datetime(2026, 6, 29, 15, 30, tzinfo=MSK)
    text = format_datetime_context(fixed)
    assert "2026-06-29 15:30" in text
    assert "МСК" in text
    assert "понедельник" in text


def test_prepend_datetime_context_adds_block():
    out = prepend_datetime_context("Ты ассистент.")
    assert out.startswith("Ты ассистент.")
    assert "Сейчас:" in out


def test_prepend_datetime_context_idempotent():
    once = prepend_datetime_context("prompt")
    twice = prepend_datetime_context(once)
    assert twice.count("Сейчас:") == 1
