"""Общие метки «куда отправили» и разбор цели для тест-команд."""

from __future__ import annotations

from typing import Literal, Optional

SendTarget = Literal["dm", "group"]

GROUP_TARGET_WORDS = frozenset(
    {"группа", "группу", "топик", "topic", "club", "group", "g"}
)
DM_TARGET_WORDS = frozenset({"личка", "личку", "dm", "private", "priv", "л"})


def parse_send_target_first_token(args: Optional[str]) -> Optional[SendTarget]:
    """Первый токен аргументов: ``личка`` / ``группа`` и синонимы."""
    token = ((args or "").strip().split() or [""])[0].lower()
    if not token:
        return None
    if token in GROUP_TARGET_WORDS:
        return "group"
    if token in DM_TARGET_WORDS:
        return "dm"
    return None


def where_dm() -> str:
    return "в личку"


def where_digest_topic() -> str:
    return "в топик дайджеста"


def where_digest_topic_with_id(topic_id: int) -> str:
    return f"в топик {topic_id} группы"
