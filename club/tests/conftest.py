"""Общие фикстуры для тестов клубного бота."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

from bot.services.member_goals_merge import merge_stated_goals_fragment


def patch_frozen_config(monkeypatch, module, **overrides):
    """Подмена полей frozen dataclass config в целевом модуле."""
    new_cfg = dataclasses.replace(module.config, **overrides)
    monkeypatch.setattr(module, "config", new_cfg)
    return new_cfg


@dataclass
class FakeUserStorage:
    """Минимальный in-memory storage для member-профиля."""

    licenses: Dict[int, bool] = field(default_factory=dict)
    profiles: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    llm_logs: List[Dict[str, Any]] = field(default_factory=list)
    interaction_logs: List[Dict[str, Any]] = field(default_factory=list)

    async def user_has_active_license(self, user_id: int) -> bool:
        return bool(self.licenses.get(user_id))

    async def get_member_profile(self, user_id: int) -> Optional[Dict[str, Any]]:
        return self.profiles.get(user_id)

    async def append_member_stated_goals_fragment(
        self,
        user_id: int,
        fragment: str,
        *,
        source: str = "llm_extract",
    ) -> bool:
        prof = self.profiles.setdefault(user_id, {"stated_goals": ""})
        current = prof.get("stated_goals") or ""
        merged, changed = merge_stated_goals_fragment(current, fragment)
        if not changed:
            return False
        prof["stated_goals"] = merged
        return True

    async def touch_member_group_activity(self, user_id: int) -> None:
        prof = self.profiles.setdefault(user_id, {})
        prof["last_group_activity_at"] = "touched"

    async def log_llm_completion_usage(self, **kwargs) -> None:
        self.llm_logs.append(kwargs)

    async def log_interaction(self, **kwargs) -> None:
        self.interaction_logs.append(kwargs)


@pytest.fixture
def fake_storage() -> FakeUserStorage:
    return FakeUserStorage()
