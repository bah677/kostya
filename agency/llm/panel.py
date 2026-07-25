"""Мульти-LLM панель Bible Bot Manager."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from db.repo import AgencyRepo
from llm.clients import LlmHub, LlmResult

logger = logging.getLogger(__name__)


def _strip_json(raw: str) -> str:
    t = (raw or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def parse_json_obj(raw: str) -> Dict[str, Any]:
    try:
        data = json.loads(_strip_json(raw))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


ANALYST_SYSTEM = """\
Ты Analyst агентства. Анализируешь KPI Библейского бота, CTA (показы/клики доната),
переходы в клуб и выборки диалогов.

Верни СТРОГО JSON:
{
  "hypotheses": [{"text": "...", "evidence": "..."}],
  "actions": [
    {
      "title": "короткий заголовок",
      "body": "конкретное проверяемое действие: ЧТО меняем и ГДЕ (файл/сценарий/промпт)",
      "evidence": "цифры/паттерны из контекста",
      "kpi_impact": "как именно это должно сдвинуть stickiness / donations / club_transitions и почему",
      "how_to_verify": "как через 1–7 дней проверить эффект по KPI/CTA",
      "target_system": "biblia|club|content|agency",
      "priority": 1
    }
  ],
  "data_gaps": ["человекочитаемое описание пробела, не сырой key"],
  "handoffs": [
    {"to_agent_id": "copywriter|producer|club_bot_manager", "subject": "...", "body": "..."}
  ]
}
1–3 actions. Без абстракций вроде «сделай теплее».
Не выдумывай метрики: опирайся только на CONTEXT.
Если CTA/переходы уже есть в CONTEXT — не пиши, что «данных нет».
"""

CRITIC_SYSTEM = """\
Ты Critic. Жёстко проверяешь actions Analyst на:
- проверяемость, этический риск уязвимой аудитории, техническую реализуемость,
- дубли прошлых рекомендаций,
- наличие kpi_impact и how_to_verify.
Верни JSON:
{
  "keep": [{
    "title": "...",
    "body": "...",
    "evidence": "...",
    "kpi_impact": "...",
    "how_to_verify": "...",
    "target_system": "biblia",
    "priority": 1
  }],
  "drop": [{"title": "...", "reason": "..."}],
  "warnings": ["..."]
}
Можно переписать body/kpi_impact, сохранив суть.
"""

ALTERNATIVE_SYSTEM = """\
Ты Alternative. Дай другой угол: что Analyst мог пропустить.
JSON: {"extra_actions": [{
  "title","body","evidence","kpi_impact","how_to_verify","target_system","priority"
}], "notes": ["..."]}
Максимум 2 extra_actions.
"""

EDITOR_SYSTEM = """\
Ты Editor ежедневного brief для человека. Склей итог.
Верни JSON:
{
  "brief_md": "markdown на русском; читается 1–2 минуты, но рекомендации развёрнутые",
  "actions": [{
    "title","body","evidence","kpi_impact","how_to_verify","target_system","priority"
  }],
  "data_gaps": ["понятные фразы на русском, не сырые ключи"],
  "handoffs": [{"to_agent_id","subject","body"}]
}
Структура brief_md:
## Вчера
KPI + CTA (показы/клики) если есть в контексте. Не противоречь цифрам шапки.
## Память
что было и сработало / нет
## Сегодня (1–3)
Для каждого пункта:
**N. title**
- Что сделать: ...
- Почему / evidence: ...
- Влияние на KPI: ...
- Как проверить: ...
## Пробелы данных
Только реальные пробелы простым языком (где таблица/что отсутствует/зачем мешает).
Не выдумывай пробелы про CTA, если цифры CTA уже есть.
В конце одна строка: «Можно ответить реплаем на это сообщение — обсудим и при необходимости пересоберём рекомендации.»
"""


async def _log(repo: Optional[AgencyRepo], run_id, agent_id, role, res: LlmResult):
    if not repo:
        return
    await repo.log_llm_call(
        run_id=run_id,
        agent_id=agent_id,
        provider=res.provider,
        model=res.model,
        role_in_panel=role,
        has_web=res.has_web,
        prompt_tokens=res.prompt_tokens,
        completion_tokens=res.completion_tokens,
        latency_ms=res.latency_ms,
        ok=res.ok,
        error_text=res.error,
    )


async def run_panel(
    hub: LlmHub,
    *,
    repo: AgencyRepo,
    run_id: int,
    agent_id: str,
    context_blob: str,
    research_query: str,
) -> Dict[str, Any]:
    panel: Dict[str, Any] = {
        "analyst": None,
        "researcher": None,
        "critic": None,
        "alternative": None,
        "editor": None,
        "degraded": [],
    }

    # Researcher (web) first — feeds analyst
    research_text = ""
    if hub.has_web:
        res = await hub.web_research(research_query)
        await _log(repo, run_id, agent_id, "researcher", res)
        panel["researcher"] = {"ok": res.ok, "text": res.text, "error": res.error}
        if res.ok and res.text:
            research_text = res.text
            await repo.add_external_signal(
                run_id=run_id,
                agent_id=agent_id,
                signal_date=__import__("datetime").date.today(),
                summary=res.text[:4000],
                title="web_research",
                relevance="bible_bot_manager_daily",
            )
        else:
            panel["degraded"].append("researcher")
            await repo.upsert_data_gap(
                gap_key="web_research_unavailable",
                description="Researcher/web не отработал — внешние бенчмарки пропущены.",
                severity="low",
            )
    else:
        panel["degraded"].append("researcher_no_key")

    user_ctx = context_blob
    if research_text:
        user_ctx += f"\n\n<<<WEB_RESEARCH>>>\n{research_text[:5000]}\n<<<END>>>"

    # Analyst — prefer DeepSeek, else OpenAI
    analyst_raw = ""
    if hub.has_deepseek:
        res = await hub.chat(
            provider="deepseek",
            model=hub.cfg.DEEPSEEK_MODEL,
            system=ANALYST_SYSTEM,
            user=user_ctx,
            temperature=0.25,
            max_tokens=2200,
        )
        await _log(repo, run_id, agent_id, "analyst", res)
        if res.ok:
            analyst_raw = res.text
            panel["analyst"] = parse_json_obj(res.text) or {"raw": res.text}
        else:
            panel["degraded"].append("analyst_deepseek")
    if not analyst_raw and hub.has_openai:
        res = await hub.chat(
            provider="openai",
            model=hub.cfg.OPENAI_MODEL,
            system=ANALYST_SYSTEM,
            user=user_ctx,
            temperature=0.25,
            max_tokens=2200,
        )
        await _log(repo, run_id, agent_id, "analyst", res)
        if res.ok:
            analyst_raw = res.text
            panel["analyst"] = parse_json_obj(res.text) or {"raw": res.text}
        else:
            panel["degraded"].append("analyst_openai")

    if not analyst_raw:
        # deterministic fallback
        panel["degraded"].append("analyst_all")
        fallback = {
            "hypotheses": [],
            "actions": [
                {
                    "title": "Проверить покрытие KPI3",
                    "body": (
                        "LLM недоступны. Вручную сверить DAU/донаты с /report библии "
                        "и переходы biblia_bot в club attribution."
                    ),
                    "evidence": "panel_degraded",
                    "target_system": "agency",
                    "priority": 1,
                }
            ],
            "data_gaps": ["llm_panel_unavailable"],
            "handoffs": [],
        }
        panel["analyst"] = fallback
        analyst_obj = fallback
    else:
        analyst_obj = panel["analyst"] if isinstance(panel["analyst"], dict) else {}

    # Critic — Anthropic preferred, else OpenAI
    critic_input = json.dumps(analyst_obj, ensure_ascii=False)[:8000]
    critic_obj: Dict[str, Any] = {}
    if hub.has_anthropic:
        res = await hub.chat(
            provider="anthropic",
            model=hub.cfg.ANTHROPIC_MODEL,
            system=CRITIC_SYSTEM,
            user=f"CONTEXT:\n{context_blob[:6000]}\n\nANALYST:\n{critic_input}",
            temperature=0.2,
            max_tokens=1800,
        )
        await _log(repo, run_id, agent_id, "critic", res)
        if res.ok:
            critic_obj = parse_json_obj(res.text)
            panel["critic"] = critic_obj or {"raw": res.text}
        else:
            panel["degraded"].append("critic")
    elif hub.has_openai:
        res = await hub.chat(
            provider="openai",
            model=hub.cfg.OPENAI_MODEL,
            system=CRITIC_SYSTEM,
            user=f"ANALYST:\n{critic_input}",
            temperature=0.2,
            max_tokens=1800,
        )
        await _log(repo, run_id, agent_id, "critic", res)
        if res.ok:
            critic_obj = parse_json_obj(res.text)
            panel["critic"] = critic_obj or {"raw": res.text}
        else:
            panel["degraded"].append("critic")
    else:
        panel["degraded"].append("critic_skipped")

    # Alternative — OpenAI if available
    alt_obj: Dict[str, Any] = {}
    if hub.has_openai:
        res = await hub.chat(
            provider="openai",
            model=hub.cfg.OPENAI_MODEL,
            system=ALTERNATIVE_SYSTEM,
            user=f"CONTEXT short:\n{context_blob[:4000]}\nANALYST:\n{critic_input}",
            temperature=0.4,
            max_tokens=1200,
        )
        await _log(repo, run_id, agent_id, "alternative", res)
        if res.ok:
            alt_obj = parse_json_obj(res.text)
            panel["alternative"] = alt_obj or {"raw": res.text}
        else:
            panel["degraded"].append("alternative")

    # Editor
    merge_payload = {
        "analyst": analyst_obj,
        "critic": critic_obj,
        "alternative": alt_obj,
        "research": research_text[:3000],
    }
    editor_obj: Dict[str, Any] = {}
    editor_provider = "openai" if hub.has_openai else ("deepseek" if hub.has_deepseek else "")
    editor_model = hub.cfg.OPENAI_MODEL if hub.has_openai else hub.cfg.DEEPSEEK_MODEL
    if editor_provider:
        res = await hub.chat(
            provider=editor_provider,
            model=editor_model,
            system=EDITOR_SYSTEM,
            user=json.dumps(merge_payload, ensure_ascii=False)[:14000],
            temperature=0.2,
            max_tokens=2000,
        )
        await _log(repo, run_id, agent_id, "editor", res)
        if res.ok:
            editor_obj = parse_json_obj(res.text)
            panel["editor"] = editor_obj or {"raw": res.text, "brief_md": res.text}
        else:
            panel["degraded"].append("editor")

    if not editor_obj.get("brief_md"):
        # assemble minimal brief
        actions = critic_obj.get("keep") or analyst_obj.get("actions") or []
        lines = ["## Вчера\n(см. KPI в контексте)", "", "## Сегодня"]
        for i, a in enumerate(actions[:3], 1):
            lines.append(f"{i}. **{a.get('title','')}** — {a.get('body','')}")
        editor_obj = {
            "brief_md": "\n".join(lines),
            "actions": actions[:3],
            "data_gaps": analyst_obj.get("data_gaps") or [],
            "handoffs": analyst_obj.get("handoffs") or [],
        }
        panel["editor"] = editor_obj

    panel["final"] = editor_obj
    return panel
