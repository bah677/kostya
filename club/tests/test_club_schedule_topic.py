"""Тесты топика расписания в админ-группе."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from bot.services.club_schedule_extractor import (
    schedule_topic_input_eligible,
    vision_media_text,
)
from bot.services.club_schedule_service import (
    ScheduleApplyResult,
    format_schedule_topic_digest,
    schedule_topic_reply_html,
)

MSK = ZoneInfo("Europe/Moscow")


class ScheduleStorage:
    def __init__(self, events=None):
        self.events = events or []

    async def list_club_schedule_events(self, *, from_at, to_at):
        return self.events


@pytest.mark.asyncio
async def test_format_topic_digest_empty():
    storage = ScheduleStorage([])
    body = await format_schedule_topic_digest(storage, days=14)
    assert "14 дней" in body
    assert "базе пока нет" in body
    assert "напишите здесь" in body.lower() or "Напишите" in body


@pytest.mark.asyncio
async def test_format_topic_digest_with_events():
    storage = ScheduleStorage(
        [
            {
                "starts_at": datetime(2026, 6, 1, 19, 0, tzinfo=MSK),
                "title": "Эфир про молитву",
                "content_type": "air",
            }
        ]
    )
    body = await format_schedule_topic_digest(storage, days=7)
    assert "Эфир про молитву" in body
    assert "01.06" in body
    assert "эфир" in body


def test_schedule_topic_reply_applied():
    html = schedule_topic_reply_html(
        ScheduleApplyResult(applied=True, summary="01.06 19:00 — эфир: Тест", event_ids=[1])
    )
    assert "Записал" in html
    assert "01.06" in html


def test_schedule_topic_reply_not_understood():
    html = schedule_topic_reply_html(None)
    assert "Не смог разобрать" in html


def test_vision_media_text_detects_photo_description():
    assert vision_media_text("[фото: Понедельник 19:00 эфир]")
    assert not vision_media_text("просто текст")


def test_schedule_topic_input_accepts_vision_output():
    assert schedule_topic_input_eligible("[фото: таблица расписания на неделю]")
