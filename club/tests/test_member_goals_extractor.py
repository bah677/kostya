"""Тесты извлечения stated_goals из реплик участника."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.services.member_goals_extractor import (
    _history_user_snippet,
    _strip_json_fence,
    extract_and_append_member_goals,
)
import bot.services.member_goals_extractor as goals_extractor_mod
from tests.conftest import patch_frozen_config


def test_strip_json_fence():
    raw = '```json\n{"action": "none"}\n```'
    assert _strip_json_fence(raw) == '{"action": "none"}'


def test_history_user_snippet_labels():
    history = [
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "Здравствуйте"},
        {"role": "user", "content": "Хочу больше молитвы"},
    ]
    snip = _history_user_snippet(history, max_msgs=2)
    assert "Менеджер: Здравствуйте" in snip
    assert "Участник: Хочу больше молитвы" in snip
    assert "Привет" not in snip


@pytest.mark.asyncio
async def test_extract_skips_without_license(fake_storage, monkeypatch):
    patch_frozen_config(monkeypatch, goals_extractor_mod, MEMBER_GOALS_EXTRACT_ENABLED=True)
    fake_storage.licenses[1] = False
    llm = MagicMock()
    ok = await extract_and_append_member_goals(
        user_storage=fake_storage,
        llm_client=llm,
        user_id=1,
        user_message="хочу больше молитвы",
    )
    assert ok is False
    llm.chat.completions.create.assert_not_called()


@pytest.mark.asyncio
async def test_extract_none_action_no_db_update(fake_storage, monkeypatch):
    patch_frozen_config(monkeypatch, goals_extractor_mod, MEMBER_GOALS_EXTRACT_ENABLED=True)
    fake_storage.licenses[42] = True
    fake_storage.profiles[42] = {"stated_goals": ""}

    llm = MagicMock()
    llm.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"action": "none", "fragment": "", "reason": "привет"}'
                    )
                )
            ],
            usage=None,
        )
    )

    ok = await extract_and_append_member_goals(
        user_storage=fake_storage,
        llm_client=llm,
        user_id=42,
        user_message="привет",
    )
    assert ok is False
    assert fake_storage.profiles[42].get("stated_goals") in ("", None)


@pytest.mark.asyncio
async def test_extract_append_updates_profile(fake_storage, monkeypatch):
    patch_frozen_config(monkeypatch, goals_extractor_mod, MEMBER_GOALS_EXTRACT_ENABLED=True)
    fake_storage.licenses[7] = True
    fake_storage.profiles[7] = {"stated_goals": "уже есть цель"}

    llm = MagicMock()
    llm.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            '{"action": "append", "fragment": "важна семейная молитва", '
                            '"reason": "явный запрос"}'
                        )
                    )
                )
            ],
            usage=None,
        )
    )

    ok = await extract_and_append_member_goals(
        user_storage=fake_storage,
        llm_client=llm,
        user_id=7,
        user_message="мне важна семейная молитва каждый вечер",
        history=[{"role": "user", "content": "раньше писал"}],
    )
    assert ok is True
    goals = fake_storage.profiles[7]["stated_goals"]
    assert "уже есть цель" in goals
    assert "семейная молитва" in goals


@pytest.mark.asyncio
async def test_extract_disabled_by_config(fake_storage, monkeypatch):
    patch_frozen_config(monkeypatch, goals_extractor_mod, MEMBER_GOALS_EXTRACT_ENABLED=False)
    fake_storage.licenses[1] = True
    llm = MagicMock()
    ok = await extract_and_append_member_goals(
        user_storage=fake_storage,
        llm_client=llm,
        user_id=1,
        user_message="хочу больше молитвы в жизни",
    )
    assert ok is False
