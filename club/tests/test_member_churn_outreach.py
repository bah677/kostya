"""Тесты churn outreach: шаблон +18 и fallback при выключенном AI."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.services.member_churn_outreach import generate_churn_outreach_html
from bot.texts import ru_subscription_reminder as sub_txt
import bot.services.member_churn_outreach as churn_mod
from tests.conftest import patch_frozen_config


def _churn_block(slug: str, days: int) -> dict:
    for block in sub_txt.CHURN_MESSAGES:
        if block["slug"] == slug:
            return block
    raise KeyError(slug)


@pytest.mark.asyncio
async def test_churn_plus_18_always_template(fake_storage, monkeypatch):
    """Опрос +18 — только шаблон, LLM не вызывается."""
    patch_frozen_config(monkeypatch, churn_mod, MEMBER_CHURN_AI_ENABLED=True)
    block = _churn_block("churn_plus_18d", 18)
    expected = sub_txt.personalize_html(block["text"], "Анна")

    llm = MagicMock()
    llm.chat.completions.create = AsyncMock(side_effect=AssertionError("LLM must not run"))

    body = await generate_churn_outreach_html(
        user_storage=fake_storage,
        llm_client=llm,
        rag_stack=None,
        user_id=100,
        first_name="Анна",
        churn_block=block,
    )
    assert body == expected
    assert "1." in body and "6." in body


@pytest.mark.asyncio
async def test_churn_ai_disabled_uses_template(fake_storage, monkeypatch):
    patch_frozen_config(monkeypatch, churn_mod, MEMBER_CHURN_AI_ENABLED=False)
    block = _churn_block("churn_plus_5d", 5)
    expected = sub_txt.personalize_html(block["text"], "Иван")

    llm = MagicMock()
    llm.chat.completions.create = AsyncMock(side_effect=AssertionError("LLM must not run"))

    body = await generate_churn_outreach_html(
        user_storage=fake_storage,
        llm_client=llm,
        rag_stack=None,
        user_id=101,
        first_name="Иван",
        churn_block=block,
    )
    assert body == expected


@pytest.mark.asyncio
async def test_churn_plus_5_ai_path(fake_storage, monkeypatch):
    patch_frozen_config(
        monkeypatch,
        churn_mod,
        MEMBER_CHURN_AI_ENABLED=True,
        MEMBER_AGENT_VERIFIER_ENABLED=False,
    )
    monkeypatch.setattr(
        "bot.services.member_churn_outreach.fetch_schedule_for_prompt",
        AsyncMock(return_value=""),
    )

    block = _churn_block("churn_plus_5d", 5)
    fake_storage.profiles[102] = {"stated_goals": "молитва"}

    llm = MagicMock()
    llm.chat.completions.create = AsyncMock(
        return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content="<b>Привет</b>, как ты?"))],
            usage=None,
        )
    )

    body = await generate_churn_outreach_html(
        user_storage=fake_storage,
        llm_client=llm,
        rag_stack=None,
        user_id=102,
        first_name="Мария",
        churn_block=block,
    )
    assert body == "<b>Привет</b>, как ты?"
    llm.chat.completions.create.assert_awaited_once()
