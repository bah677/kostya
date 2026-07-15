"""Общие типы для политик доступа к боту."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AccessDecision(str, Enum):
    """Результат проверки доступа после журналирования апдейта."""

    # Публичные маршруты (команда / callback-префиксы) — доступ без проверки бана/подписки.
    ALLOW_PUBLIC = "allow_public"
    # Обычный доступ разрешён (прошли бан/whitelist/и т. д.).
    ALLOW = "allow"
    # Доступ закрыт — ответ пользователю и прерывание цепочки.
    DENY = "deny"


@dataclass(frozen=True)
class AccessContext:
    """Минимальный контекст одного входящего апдейта для политики."""

    user_id: int
    event_type: str
    # message | callback_query | edited_message | message_reaction
    raw_event_type: str
