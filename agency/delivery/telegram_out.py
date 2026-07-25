"""Отправка brief в Telegram."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiogram import Bot
    from config import Config

logger = logging.getLogger(__name__)


async def send_brief(bot: "Bot", cfg: "Config", text: str) -> None:
    chat_id = int(cfg.AGENCY_BRIEF_CHAT_ID or 0)
    if not chat_id:
        # fallback: каждому админу
        targets = list(cfg.AGENCY_ADMIN_IDS)
    else:
        targets = [chat_id]

    if not targets:
        logger.warning("no AGENCY_BRIEF_CHAT_ID / AGENCY_ADMIN_IDS — brief not sent")
        return

    # Telegram limit 4096
    chunks = _chunk(text, 3500)
    for tid in targets:
        for i, chunk in enumerate(chunks):
            try:
                await bot.send_message(tid, chunk if i == 0 else f"(cont.)\n{chunk}")
            except Exception as e:
                logger.error("send brief to %s failed: %s", tid, e)


def _chunk(text: str, size: int) -> list:
    if len(text) <= size:
        return [text]
    parts = []
    while text:
        parts.append(text[:size])
        text = text[size:]
    return parts
