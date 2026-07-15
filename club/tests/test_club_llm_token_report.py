"""Тесты отчёта по LLM-токенам."""

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from bot.services.club_llm_token_report import (
    build_llm_token_report_html,
    yesterday_msk,
)

MSK = ZoneInfo("Europe/Moscow")


def test_yesterday_msk_at_midnight_boundary(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz == MSK:
                return cls(2026, 6, 29, 0, 1, tzinfo=MSK)
            return cls.now.__func__(cls, tz)

    import bot.services.club_llm_token_report as mod

    monkeypatch.setattr(mod, "datetime", FixedDatetime)
    assert yesterday_msk() == date(2026, 6, 28)


@pytest.mark.asyncio
async def test_build_llm_token_report_uses_msk_date():
    storage = MagicMock()
    storage.get_global_token_stats_for_msk_date = AsyncMock(
        return_value={
            "total": {
                "total_requests": 12,
                "unique_users": 5,
                "total_prompt_tokens": 1000,
                "total_completion_tokens": 200,
                "total_tokens": 1200,
            },
            "by_provider_request_model": [
                {
                    "provider": "deepseek",
                    "request_kind": "club_outreach_policy",
                    "model": "deepseek-chat",
                    "total_tokens": 800,
                    "request_count": 8,
                }
            ],
            "top_users": [],
        }
    )
    html = await build_llm_token_report_html(storage, report_date=date(2026, 6, 28))
    storage.get_global_token_stats_for_msk_date.assert_awaited_once_with(date(2026, 6, 28))
    assert "28.06.2026" in html
    assert "Календарный день по МСК" in html
    assert "1 200" in html
    assert "club_outreach_policy" in html
