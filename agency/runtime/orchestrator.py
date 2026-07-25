"""Ночной оркестратор агентства."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from agents.bible_bot_manager.agent import run_bible_bot_manager
from agents.qa_manager.agent import run_qa_manager
from collectors.windows import yesterday_msk
from config import Config
from db.pool import Pools

logger = logging.getLogger(__name__)

AGENTS = ("bible_bot_manager", "qa_manager", "all")


async def run_nightly(
    *,
    cfg: Config,
    pools: Pools,
    bot=None,
    day: Optional[date] = None,
    skip_llm: bool = False,
    agent: str = "all",
) -> dict:
    """
    Порядок: Bible Bot Manager → QA Manager.
    agent: bible_bot_manager | qa_manager | all
    """
    target = day or yesterday_msk()
    which = (agent or "all").strip().lower()
    if which in ("bible", "bbm"):
        which = "bible_bot_manager"
    if which in ("qa", "qa_mgr"):
        which = "qa_manager"
    logger.info(
        "orchestrator start day=%s skip_llm=%s agent=%s", target, skip_llm, which
    )

    results: Dict[str, Any] = {"day": target.isoformat(), "agents": {}}
    briefs: List[str] = []

    if which in ("all", "bible_bot_manager"):
        r = await run_bible_bot_manager(
            cfg=cfg,
            pools=pools,
            day=target,
            skip_llm=skip_llm,
            bot=bot,
        )
        results["agents"]["bible_bot_manager"] = r
        results["run_id"] = r.get("run_id")
        results["status"] = r.get("status")
        if r.get("brief"):
            briefs.append(r["brief"])

    if which in ("all", "qa_manager"):
        r = await run_qa_manager(
            cfg=cfg,
            pools=pools,
            day=target,
            skip_llm=skip_llm,
            bot=bot,
        )
        results["agents"]["qa_manager"] = r
        results["qa_run_id"] = r.get("run_id")
        # общий status: failed если любой failed, иначе degraded/ok
        st = r.get("status")
        prev = results.get("status")
        if st == "failed" or prev == "failed":
            results["status"] = "failed"
        elif st == "degraded" or prev == "degraded":
            results["status"] = "degraded"
        else:
            results["status"] = st or prev or "ok"
        if r.get("brief"):
            briefs.append(r["brief"])

    results["brief"] = "\n\n---\n\n".join(briefs) if briefs else ""
    logger.info("orchestrator done status=%s agents=%s", results.get("status"), list(results["agents"]))
    return results
