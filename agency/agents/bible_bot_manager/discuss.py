"""Обсуждение daily brief реплаем — сильная модель решает, что ответить/пересобрать."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Dict, List, Optional

from config import Config
from db.pool import Pools
from db.repo import AgencyRepo
from llm.clients import LlmHub
from llm.panel import parse_json_obj

logger = logging.getLogger(__name__)

AGENT_ID = "bible_bot_manager"

DISCUSS_SYSTEM = """\
Ты старший менеджер продукта Bible Bot в диалоге с владельцем.
Тебе дан вчерашний daily brief, текущие рекомендации и реплика человека (reply).

Задача:
1) Ответить по делу, как живому коллеге.
2) Если человек просит уточнить / оспорить / переделать — предложи обновлённые recommendations.
3) Не выдумывай KPI; опирайся на brief и данные.

Верни СТРОГО JSON:
{
  "reply_text": "ответ человеку на русском (можно markdown-ish, без ```)",
  "intent": "question|challenge|revise|approve|other",
  "replace_recommendations": false,
  "new_actions": [
    {
      "title": "...",
      "body": "...",
      "evidence": "...",
      "kpi_impact": "...",
      "how_to_verify": "...",
      "target_system": "biblia|club|content|agency",
      "priority": 1
    }
  ],
  "abandon_recommendation_ids": [123],
  "notes": "внутрь"
}
replace_recommendations=true только если явно нужно заменить/пересобрать список.
new_actions: 0–3 шт.
"""


async def discuss_brief_reply(
    *,
    cfg: Config,
    pools: Pools,
    chat_id: int,
    reply_to_message_id: int,
    user_id: int,
    user_text: str,
) -> str:
    assert pools.agency
    repo = AgencyRepo(pools.agency)
    brief_row = await repo.find_brief_by_reply(chat_id, reply_to_message_id)
    if not brief_row:
        return (
            "Не вижу связанный daily brief для этого реплая. "
            "Ответьте именно на сообщение отчёта агентства."
        )

    run_id = int(brief_row["run_id"])
    agent_id = str(brief_row.get("agent_id") or AGENT_ID)
    brief_text = await repo.get_run_brief_text(run_id)
    recs = await repo.get_run_recommendations(run_id)
    prior = await repo.recent_discussions_for_run(run_id, limit=6)
    prior_blob = "\n".join(
        f"USER: {p['user_text'][:500]}\nASSISTANT: {p['assistant_text'][:800]}"
        for p in reversed(prior)
    )
    recs_blob = json.dumps(
        [
            {
                "id": r["id"],
                "status": r["status"],
                "title": r["title"],
                "body": r["body"],
                "meta": r.get("meta_json"),
            }
            for r in recs
        ],
        ensure_ascii=False,
        default=str,
    )[:8000]

    user_block = (
        f"RUN_ID={run_id} DATE={brief_row.get('run_date')}\n\n"
        f"<<<BRIEF>>>\n{brief_text[:9000]}\n<<<END>>>\n\n"
        f"<<<RECOMMENDATIONS>>>\n{recs_blob}\n<<<END>>>\n\n"
        f"<<<PRIOR_THREAD>>>\n{prior_blob or '(пусто)'}\n<<<END>>>\n\n"
        f"<<<HUMAN_REPLY>>>\n{user_text}\n<<<END>>>"
    )

    hub = LlmHub(cfg)
    res = await _call_strong(hub, cfg, user_block)
    await repo.log_llm_call(
        run_id=run_id,
        agent_id=agent_id,
        provider=res.provider,
        model=res.model,
        role_in_panel="discuss",
        has_web=False,
        prompt_tokens=res.prompt_tokens,
        completion_tokens=res.completion_tokens,
        latency_ms=res.latency_ms,
        ok=res.ok,
        error_text=res.error,
    )

    if not res.ok or not res.text:
        return f"Не удалось разобрать реплай (LLM: {res.error or 'empty'})."

    data = parse_json_obj(res.text)
    reply = (data.get("reply_text") or res.text or "").strip()
    if not reply:
        reply = res.text.strip()[:3500]

    # apply recommendation changes if requested
    actions_meta: Dict[str, Any] = {"intent": data.get("intent"), "raw_ok": True}
    if data.get("replace_recommendations") or data.get("new_actions"):
        abandon_ids = data.get("abandon_recommendation_ids") or []
        for rid in abandon_ids:
            try:
                await repo.set_recommendation_status(
                    int(rid), "abandoned", actor_user_id=user_id, note="discuss_reply"
                )
            except Exception:
                pass
        new_actions = data.get("new_actions") or []
        created = []
        run_day = brief_row.get("run_date")
        if not isinstance(run_day, date):
            run_day = date.today()
        for a in new_actions[:3]:
            if not isinstance(a, dict):
                continue
            rid = await repo.add_recommendation(
                agent_id=agent_id,
                run_id=run_id,
                created_on=run_day,
                title=str(a.get("title") or "revised")[:300],
                body=_format_action_body(a),
                evidence=str(a.get("evidence") or ""),
                target_system=str(a.get("target_system") or "biblia"),
                priority=int(a.get("priority") or 2),
                meta={
                    "kpi_impact": a.get("kpi_impact"),
                    "how_to_verify": a.get("how_to_verify"),
                    "from_discuss": True,
                },
            )
            created.append(rid)
        actions_meta["created_recommendation_ids"] = created
        if created:
            reply += "\n\n📌 Обновил рекомендации: " + ", ".join(
                f"#{i}" for i in created
            )
            reply += "\nСмотри /agency_recs"

    await repo.add_brief_discussion(
        run_id=run_id,
        agent_id=AGENT_ID,
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id,
        user_id=user_id,
        user_text=user_text,
        assistant_text=reply,
        actions=actions_meta,
    )
    return reply[:4000]


def _format_action_body(a: dict) -> str:
    parts = [str(a.get("body") or "").strip()]
    if a.get("kpi_impact"):
        parts.append(f"Влияние на KPI: {a['kpi_impact']}")
    if a.get("how_to_verify"):
        parts.append(f"Как проверить: {a['how_to_verify']}")
    return "\n".join(p for p in parts if p)


async def _call_strong(hub: LlmHub, cfg: Config, user_block: str):
    # приоритет: Anthropic (critic-класс) → OpenAI strong → DeepSeek
    if hub.has_anthropic:
        return await hub.chat(
            provider="anthropic",
            model=cfg.ANTHROPIC_MODEL,
            system=DISCUSS_SYSTEM,
            user=user_block,
            temperature=0.3,
            max_tokens=2500,
        )
    strong = getattr(cfg, "OPENAI_STRONG_MODEL", None) or "gpt-4o"
    if hub.has_openai:
        return await hub.chat(
            provider="openai",
            model=strong,
            system=DISCUSS_SYSTEM,
            user=user_block,
            temperature=0.3,
            max_tokens=2500,
        )
    return await hub.chat(
        provider="deepseek",
        model=cfg.DEEPSEEK_MODEL,
        system=DISCUSS_SYSTEM,
        user=user_block,
        temperature=0.3,
        max_tokens=2500,
    )
