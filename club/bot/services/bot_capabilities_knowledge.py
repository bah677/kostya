"""Загрузка документации о возможностях бота для клубного агента."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def load_member_bot_capabilities() -> str:
    """Текст для member-агента: только клиентские возможности бота."""
    candidates = (
        _REPO_ROOT / "bot" / "texts" / "bot_capabilities_member.txt",
        _REPO_ROOT / "bot_capabilities_member.txt",
    )
    for path in candidates:
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8").strip()
                logger.info(
                    "Загружены возможности бота для member-агента %s: %s символов",
                    path,
                    len(content),
                )
                return content
            except OSError as e:
                logger.error("Не удалось прочитать %s: %s", path, e)
    logger.warning("bot_capabilities_member.txt не найден")
    return ""
