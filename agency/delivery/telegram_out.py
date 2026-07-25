"""Отправка brief в Telegram + регистрация message_id для reply."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from aiogram import Bot
    from config import Config
    from db.repo import AgencyRepo

logger = logging.getLogger(__name__)


async def send_brief(
    bot: "Bot",
    cfg: "Config",
    text: str,
    *,
    repo: Optional["AgencyRepo"] = None,
    run_id: Optional[int] = None,
    agent_id: str = "bible_bot_manager",
) -> List[int]:
    """Returns list of telegram message_ids sent."""
    chat_id = int(cfg.AGENCY_BRIEF_CHAT_ID or 0)
    if not chat_id:
        targets = list(cfg.AGENCY_ADMIN_IDS)
        if cfg.SUPER_ADMIN_ID:
            targets = list({*targets, int(cfg.SUPER_ADMIN_ID)})
    else:
        targets = [chat_id]

    if not targets:
        logger.warning("no AGENCY_BRIEF_CHAT_ID / admins — brief not sent")
        return []

    chunks = _chunk(text, 3500)
    sent_ids: List[int] = []
    for tid in targets:
        for i, chunk in enumerate(chunks):
            body = chunk if i == 0 else f"(cont. {i+1}/{len(chunks)})\n{chunk}"
            if i == len(chunks) - 1:
                body += (
                    "\n\n💬 Реплай на это сообщение — обсудим отчёт "
                    "и при необходимости пересоберём рекомендации."
                )
            try:
                msg = await bot.send_message(tid, body)
                mid = int(getattr(msg, "message_id", 0) or 0)
                if mid and repo is not None and run_id is not None:
                    await repo.register_brief_message(
                        run_id=run_id,
                        agent_id=agent_id,
                        chat_id=int(tid),
                        telegram_message_id=mid,
                        chunk_index=i,
                    )
                    sent_ids.append(mid)
            except Exception as e:
                logger.error("send brief to %s failed: %s", tid, e)
    return sent_ids


def _chunk(text: str, size: int) -> list:
    if len(text) <= size:
        return [text]
    parts = []
    while text:
        parts.append(text[:size])
        text = text[size:]
    return parts
