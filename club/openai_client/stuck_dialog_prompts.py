"""Совместимость: промпты перенесены в `bot.texts.prompts.stuck_dialog`."""

from bot.texts.prompts.stuck_dialog import (  # noqa: F401
    STUCK_ANALYZER_SYSTEM,
    STUCK_COMPOSE_SYSTEM,
    STUCK_RAG_PLANNER_SYSTEM,
)
