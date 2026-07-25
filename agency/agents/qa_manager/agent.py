"""QA Manager — ночной разбор error-логов → короткие ТЗ на баги."""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from collectors.error_logs import (
    DEFAULT_SPECS,
    ProjectErrorDigest,
    ProjectLogSpec,
    collect_all_digests,
    format_digest_blob,
    specs_from_config_paths,
)
from collectors.windows import yesterday_msk
from config import Config
from db.pool import Pools
from db.repo import AgencyRepo
from delivery.telegram_out import send_brief
from llm.clients import LlmHub
from llm.panel import parse_json_obj

logger = logging.getLogger(__name__)

AGENT_ID = "qa_manager"

QA_SYSTEM = """\
Ты менеджер по QA экосистемы ботов (club, biblia, avatar_kostya).
Тебе дан дайджест ERROR-логов за сутки: по проектам, уже сгруппированный (count + sample).

Задача: выделить реальные баги / дефекты кода и написать КОРОТКИЕ ТЗ.

Формат ответа — ЧИСТЫЙ ТЕКСТ (без JSON, без markdown ##, без ```), строго так:

club:
баг 1: короткое описание (×N)
короткое ТЗ в 1–2 предложениях

баг 2: короткое описание (×N)
короткое ТЗ

biblia:
баг 1: ...
короткое ТЗ

avatar_kostya:
баг 1: ...
короткое ТЗ

Правила:
- Проекты только: club, biblia, avatar_kostya — в этом порядке, даже если багов нет
  (тогда одна строка: «багов по ERROR-логам за сутки не найдено»).
- Максимум 5 багов на проект.
- Не выдумывай файлы/стек, которых нет в sample.
- Одинаковые по сути ошибки — один баг с суммарным ×N.
- USER_BOT_TO_BOT_DISABLED / blocked by user / Flood / Bad Gateway polling —
  обычно шум: не включай в баги (можно в конце блок «Шум: …»).
- Описание бага — человеческое, не копируй сырой logger name целиком.
- ТЗ: что проверить/починить, симптом, где примерно (модуль из лога).
"""


def _fallback_brief(digests: Sequence[ProjectErrorDigest]) -> str:
    lines: List[str] = []
    for d in digests:
        lines.append(f"{d.project}:")
        if not d.clusters:
            lines.append("багов по ERROR-логам за сутки не найдено")
            lines.append("")
            continue
        for i, c in enumerate(d.clusters[:5], 1):
            title = re.sub(
                r"^-\s*[\w.]+\s*-\s*ERROR\s*-\s*",
                "",
                c.signature,
                count=1,
            ).strip()[:160]
            lines.append(f"баг {i}: {title} (×{c.count})")
            lines.append(
                f"Разобрать ERROR в {d.project}: «{title[:120]}»; "
                f"воспроизвести по sample, починить первопричину."
            )
            lines.append("")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def format_qa_brief_from_json(data: Dict[str, Any], day: date) -> str:
    projects = data.get("projects") or []
    if not projects:
        return ""
    lines: List[str] = [f"QA Manager — {day.isoformat()}", ""]
    for p in projects:
        name = str(p.get("name") or "unknown").strip()
        bugs = p.get("bugs") or []
        lines.append(f"{name}:")
        if not bugs:
            lines.append("багов по ERROR-логам за сутки не найдено")
            lines.append("")
            continue
        for i, b in enumerate(bugs, 1):
            title = str(b.get("title") or "баг").strip()
            tz = str(b.get("tz") or "").strip()
            count = b.get("count")
            meta = f" (×{count})" if count is not None else ""
            lines.append(f"баг {i}: {title}{meta}")
            lines.append(tz or "Уточнить по логам")
            lines.append("")
        lines.append("")
    noise = data.get("skipped_noise") or []
    if noise:
        lines.append("Шум:")
        for n in noise[:8]:
            lines.append(f"• {n}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _looks_like_qa_brief(text: str) -> bool:
    t = (text or "").lower()
    return ("club:" in t or "biblia:" in t or "avatar" in t) and (
        "баг" in t or "багов" in t
    )


def parse_bugs_from_brief(brief: str) -> List[Dict[str, Any]]:
    """Достаёт баги из текстового brief для recommendations."""
    out: List[Dict[str, Any]] = []
    project = ""
    pending_title = ""
    pending_count: Optional[int] = None
    for raw in (brief or "").splitlines():
        line = raw.strip()
        low = line.lower().rstrip(":")
        if low in ("club", "biblia", "avatar_kostya", "avatar"):
            project = "avatar_kostya" if low == "avatar" else low
            pending_title = ""
            continue
        m = re.match(r"баг\s*\d+\s*:\s*(.+)$", line, re.I)
        if m and project:
            pending_title = m.group(1).strip()
            cm = re.search(r"\(×\s*(\d+)\)", pending_title)
            pending_count = int(cm.group(1)) if cm else None
            pending_title = re.sub(r"\s*\(×\s*\d+\)\s*$", "", pending_title).strip()
            continue
        if pending_title and project and line and not line.lower().startswith("шум"):
            out.append(
                {
                    "name": project,
                    "title": pending_title,
                    "tz": line,
                    "count": pending_count,
                }
            )
            pending_title = ""
            pending_count = None
    return out


def _resolve_specs(cfg: Config) -> List[ProjectLogSpec]:
    mapping = {
        "club": list(cfg.QA_LOG_ROOTS_CLUB),
        "biblia": list(cfg.QA_LOG_ROOTS_BIBLIA),
        "avatar_kostya": list(cfg.QA_LOG_ROOTS_AVATAR),
    }
    if not any(mapping.values()):
        return list(DEFAULT_SPECS)
    filled = {}
    defaults = {s.project: s for s in DEFAULT_SPECS}
    for proj, roots in mapping.items():
        if roots:
            filled[proj] = roots
        elif proj in defaults:
            filled[proj] = [str(r) for r in defaults[proj].roots]
    return specs_from_config_paths(filled)


async def _llm_brief(
    hub: LlmHub,
    *,
    repo: AgencyRepo,
    run_id: int,
    digest_blob: str,
    day: date,
) -> Tuple[str, Dict[str, Any], bool]:
    system = QA_SYSTEM
    user = f"TARGET_DAY={day.isoformat()}\n\nLOG_DIGEST:\n{digest_blob}"
    if hub.has_deepseek:
        res = await hub.chat(
            provider="deepseek",
            model=hub.cfg.DEEPSEEK_MODEL,
            system=system,
            user=user,
            temperature=0.2,
            max_tokens=2500,
        )
    elif hub.has_openai:
        res = await hub.chat(
            provider="openai",
            model=hub.cfg.OPENAI_MODEL,
            system=system,
            user=user,
            temperature=0.2,
            max_tokens=2500,
        )
    else:
        return "", {}, True

    await repo.log_llm_call(
        run_id=run_id,
        agent_id=AGENT_ID,
        provider=res.provider,
        model=res.model,
        role_in_panel="qa_manager",
        has_web=False,
        prompt_tokens=res.prompt_tokens,
        completion_tokens=res.completion_tokens,
        latency_ms=res.latency_ms,
        ok=res.ok,
        error_text=res.error,
    )
    if not res.ok or not (res.text or "").strip():
        return "", {}, True

    raw = res.text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```\w*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()

    header = f"QA Manager — {day.isoformat()}\n\n"
    if _looks_like_qa_brief(raw) and not raw.lstrip().startswith("{"):
        return header + raw.rstrip() + "\n", {"format": "text"}, False

    data = parse_json_obj(raw)
    if data.get("projects"):
        body = format_qa_brief_from_json(data, day)
        if body:
            return body if body.startswith("QA Manager") else header + body, data, False

    if _looks_like_qa_brief(raw):
        return header + raw.rstrip() + "\n", {"raw": raw[:2000]}, True
    return "", {"raw": raw[:2000]}, True


async def run_qa_manager(
    *,
    cfg: Config,
    pools: Pools,
    day: Optional[date] = None,
    skip_llm: bool = False,
    bot=None,
) -> Dict[str, Any]:
    assert pools.agency
    repo = AgencyRepo(pools.agency)
    target = day or yesterday_msk()
    run_id = await repo.upsert_run(AGENT_ID, target, status="running")
    status = "ok"
    try:
        specs = _resolve_specs(cfg)
        digests = collect_all_digests(target, specs, top_n=cfg.QA_TOP_CLUSTERS)
        digest_blob = format_digest_blob(digests, max_chars=cfg.QA_DIGEST_MAX_CHARS)

        await repo.put_fact(
            fact_date=target,
            fact_key="qa_error_digest",
            source_system="agency",
            value_json={
                "projects": [
                    {
                        "project": d.project,
                        "total_events": d.total_events,
                        "files_with_hits": d.files_with_hits,
                        "clusters": [
                            {
                                "signature": c.signature,
                                "count": c.count,
                                "sample": c.sample[:500],
                            }
                            for c in d.clusters
                        ],
                    }
                    for d in digests
                ]
            },
        )

        brief = _fallback_brief(digests)
        parsed: Dict[str, Any] = {}

        if not skip_llm:
            hub = LlmHub(cfg)
            llm_brief, parsed, degraded = await _llm_brief(
                hub, repo=repo, run_id=run_id, digest_blob=digest_blob, day=target
            )
            if llm_brief:
                brief = llm_brief
            if degraded:
                status = "degraded"
            await repo.add_artifact(
                run_id=run_id,
                agent_id=AGENT_ID,
                kind="panel_json",
                title="qa_llm",
                body_json=parsed,
            )

        bugs = parse_bugs_from_brief(brief)
        if not bugs and parsed.get("projects"):
            for p in parsed["projects"]:
                pname = str(p.get("name") or "").strip() or "unknown"
                for b in p.get("bugs") or []:
                    bugs.append(
                        {
                            "name": pname,
                            "title": str(b.get("title") or "bug"),
                            "tz": str(b.get("tz") or ""),
                            "count": b.get("count"),
                            "severity": b.get("severity"),
                        }
                    )

        for b in bugs:
            pname = str(b.get("name") or "unknown")
            title = str(b.get("title") or "bug")[:300]
            await repo.add_recommendation(
                agent_id=AGENT_ID,
                run_id=run_id,
                created_on=target,
                title=f"[{pname}] {title}"[:300],
                body=str(b.get("tz") or ""),
                evidence=f"count={b.get('count')}",
                target_system=pname
                if pname in ("club", "biblia", "avatar_kostya")
                else "agency",
                priority={"high": 1, "medium": 2, "low": 3}.get(
                    str(b.get("severity") or "").lower(), 2
                ),
                meta={"qa": True, "project": pname},
            )

        await repo.add_artifact(
            run_id=run_id,
            agent_id=AGENT_ID,
            kind="brief_md",
            title=f"qa_{target.isoformat()}",
            body_text=brief,
            body_json={
                "totals": {d.project: d.total_events for d in digests},
                "files": {d.project: d.files_with_hits for d in digests},
                "bugs_parsed": len(bugs),
            },
        )

        out_dir = Path(__file__).resolve().parents[2] / "data" / "runs"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{target.isoformat()}_qa_manager.md").write_text(
            brief, encoding="utf-8"
        )

        if bot is not None:
            await send_brief(
                bot, cfg, brief, repo=repo, run_id=run_id, agent_id=AGENT_ID
            )

        await repo.finish_run(
            run_id,
            status=status,
            meta={
                "totals": {d.project: d.total_events for d in digests},
                "files_hit": sum(len(d.files_with_hits) for d in digests),
                "bugs": len(bugs),
                "skip_llm": skip_llm,
            },
        )
        return {
            "run_id": run_id,
            "status": status,
            "brief": brief,
            "day": target.isoformat(),
            "agent_id": AGENT_ID,
        }
    except Exception as e:
        logger.exception("qa_manager failed")
        await repo.finish_run(run_id, status="failed", error_text=str(e)[:2000])
        raise
