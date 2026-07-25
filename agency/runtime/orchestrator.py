"""Ночной оркестратор агентства."""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from agents.bible_bot_manager.agent import run_bible_bot_manager
from collectors.windows import yesterday_msk
from config import Config
from db.pool import Pools

logger = logging.getLogger(__name__)


async def run_nightly(
    *,
    cfg: Config,
    pools: Pools,
    bot=None,
    day: Optional[date] = None,
    skip_llm: bool = False,
) -> dict:
    """
    Порядок: сначала Bible Bot Manager (единственный enabled в MVP).
    Позже сюда добавятся другие агенты по handoff/расписанию.
    """
    target = day or yesterday_msk()
    logger.info("orchestrator start day=%s skip_llm=%s", target, skip_llm)
    result = await run_bible_bot_manager(
        cfg=cfg,
        pools=pools,
        day=target,
        skip_llm=skip_llm,
        bot=bot,
    )
    logger.info("orchestrator done status=%s", result.get("status"))
    return result
