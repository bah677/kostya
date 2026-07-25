"""Bible Bot Manager — полный дневной цикл."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from collectors.content_events import correlate_content_with_kpis
from collectors.cta_metrics import collect_cta_metrics
from collectors.dialogs import format_threads_blob, sample_dialog_threads, theme_keyword_stats
from collectors.kpi import collect_with_history
from collectors.rag_context import try_rag_snippet
from config import Config
from db.pool import Pools
from db.repo import AgencyRepo
from delivery.telegram_out import send_brief
from llm.clients import LlmHub
from llm.panel import run_panel

logger = logging.getLogger(__name__)

AGENT_ID = "bible_bot_manager"


def _fmt_opt(v) -> str:
    if v is None:
        return "н/д"
    return str(v)


def _delta(cur, prev) -> str:
    if cur is None or prev is None:
        return "n/a"
    try:
        d = float(cur) - float(prev)
    except (TypeError, ValueError):
        return "n/a"
    sign = "+" if d >= 0 else ""
    if abs(d) >= 10 or abs(d - int(d)) < 1e-9:
        return f"{sign}{d:.0f}"
    return f"{sign}{d:.3f}"


def _pct(cur, avg) -> str:
    if cur is None or avg is None or float(avg) == 0:
        return "n/a"
    d = (float(cur) - float(avg)) / float(avg) * 100.0
    sign = "+" if d >= 0 else ""
    return f"{sign}{d:.1f}%"


def build_context_blob(
    *,
    kpi: Dict[str, Any],
    memory_recs: List[Dict[str, Any]],
    handoffs: List[Dict[str, Any]],
    gaps: List[Dict[str, Any]],
    threads_blob: str,
    themes: Dict[str, int],
    content_corr: Dict[str, Any],
    cta: Optional[Dict[str, Any]] = None,
    rag_bits: str = "",
) -> str:
    day = kpi["day"]
    prev = kpi["prev_day"]
    avg7 = kpi["avg7"]
    cta = cta or {}
    lines = [
        f"TARGET_DAY={day['day']}",
        "",
        "KPI_YESTERDAY:",
        f"  DAU={day['dau']} (prev {_delta(day['dau'], prev['dau'])}, vs7d {_pct(day['dau'], avg7.get('dau'))})",
        f"  MAU={day['mau']}",
        f"  stickiness(DAU/MAU)={day['stickiness']:.4f} "
        f"(prev {_delta(day['stickiness'], prev['stickiness'])}, vs7d {_pct(day['stickiness'], avg7.get('stickiness'))})",
        f"  donations_rub={day['donations_rub']} "
        f"(prev {_delta(day['donations_rub'], prev['donations_rub'])}, vs7d {_pct(day['donations_rub'], avg7.get('donations_rub'))})",
        f"  club_transitions={day['club_transitions']} "
        f"(prev {_delta(day['club_transitions'], prev['club_transitions'])}, "
        f"vs7d {_pct(day['club_transitions'], avg7.get('club_transitions'))})",
        f"  club_coverage={day.get('club_meta', {}).get('coverage_note', '')}",
        "",
        "CTA_ENGAGEMENT (biblia):",
        f"  donation_button_shows_day={cta.get('donation_button_shows_day')}",
        f"  donation_button_clicks_day={cta.get('donation_button_clicks_day')}",
        f"  donation_proposals_day={cta.get('donation_proposals_day')}",
        f"  donation_button_ctr_pct={cta.get('donation_button_ctr_pct')}",
        f"  payment_start_events={cta.get('payment_start_events')} "
        f"users={cta.get('payment_start_users')}",
        f"  marathon_open_events={cta.get('marathon_open_events')}",
        f"  note={cta.get('notes', '')}",
        "",
        "THEME_UNIQUE_USERS:",
        *(f"  {k}={v}" for k, v in themes.items()),
        "",
        "OPEN_RECOMMENDATIONS (memory):",
    ]
    if not memory_recs:
        lines.append("  (none)")
    for r in memory_recs[:15]:
        lines.append(
            f"  #{r['id']} [{r['status']}] {r['created_on']}: {r['title']}"
        )
    lines.append("")
    lines.append("PENDING_HANDOFFS_TO_ME:")
    if not handoffs:
        lines.append("  (none)")
    for h in handoffs[:10]:
        lines.append(f"  #{h['id']} from={h['from_agent_id']}: {h['subject']}")
    lines.append("")
    lines.append("OPEN_DATA_GAPS:")
    if not gaps:
        lines.append("  (none)")
    for g in gaps[:10]:
        lines.append(f"  - [{g.get('severity')}] {g['description'][:400]}")
    lines.append("")
    lines.append("CONTENT_CORRELATION:")
    lines.append(str(content_corr)[:2000])
    lines.append("")
    if rag_bits:
        lines.append("RAG_SNIPPETS:")
        lines.append(rag_bits[:2500])
        lines.append("")
    lines.append("DIALOG_SAMPLES:")
    lines.append(threads_blob[:11000])
    return "\n".join(lines)


def numeric_brief(
    kpi: Dict[str, Any],
    gaps: List[Dict[str, Any]],
    cta: Optional[Dict[str, Any]] = None,
) -> str:
    day = kpi["day"]
    prev = kpi["prev_day"]
    avg7 = kpi["avg7"]
    cta = cta or {}
    lines = [
        f"Bible Bot Manager — {day['day']} (числа)",
        "",
        f"DAU: {day['dau']} ({_delta(day['dau'], prev['dau'])} дн / {_pct(day['dau'], avg7.get('dau'))} нед)",
        f"Stickiness DAU/MAU: {day['stickiness']:.4f} "
        f"({_delta(day['stickiness'], prev['stickiness'])} / {_pct(day['stickiness'], avg7.get('stickiness'))})",
        f"Донаты: {day['donations_rub']:.0f} ₽ "
        f"({_delta(day['donations_rub'], prev['donations_rub'])} / {_pct(day['donations_rub'], avg7.get('donations_rub'))})",
        f"Переходы в клуб: {day['club_transitions']} "
        f"({_delta(day['club_transitions'], prev['club_transitions'])} / "
        f"{_pct(day['club_transitions'], avg7.get('club_transitions'))})",
        "",
        "CTA (донат/клуб):",
        f"• клики «Поддержать» (payment_start): {cta.get('payment_start_events', 0)} "
        f"событий / {cta.get('payment_start_users', 0)} уников",
        f"• выборы суммы после старта оплаты: {cta.get('payment_amount_events', 0)}",
        f"• marathon_open: {cta.get('marathon_open_events', 0)}",
        f"• показов кнопки/день: "
        + (
            f"{cta.get('donation_button_shows_day')} (источник: {cta.get('delta_source')})"
            if cta.get("donation_button_shows_day") is not None
            else f"н/д, кумулятив {cta.get('cum_shows', '?')} "
            f"(нет дневного лога показов; metric_snapshots в biblia пуст — "
            f"со 2-го ночного прогона agency посчитает дельту)"
        ),
        f"• donation_button_click/день: "
        + (
            str(cta.get("donation_button_clicks_day"))
            if cta.get("donation_button_clicks_day") is not None
            else "н/д (смотри payment_start выше)"
        ),
        "",
        "Апрув: /agency_recs · обсуждение: реплай на этот отчёт",
    ]
    if gaps:
        lines.append("")
        lines.append("Пробелы данных (понятным языком):")
        for g in gaps[:5]:
            desc = (g.get("description") or g.get("gap_key") or "").strip()
            lines.append(f"• {desc[:280]}")
    return "\n".join(lines)

async def maybe_measure_shipped(repo: AgencyRepo, kpi_today: Dict[str, Any], on: date) -> None:
    shipped = await repo.list_recommendations(
        agent_id=AGENT_ID, statuses=["shipped"], limit=20
    )
    for rec in shipped:
        days = int(rec.get("measure_after_days") or 7)
        shipped_at = rec.get("shipped_at")
        if not shipped_at:
            continue
        # measure when enough days passed since ship
        from datetime import datetime
        from zoneinfo import ZoneInfo

        if hasattr(shipped_at, "astimezone"):
            ship_day = shipped_at.astimezone(ZoneInfo("Europe/Moscow")).date()
        else:
            continue
        if on < ship_day + timedelta(days=days):
            continue
        # crude: compare target day vs ship day facts
        before = await repo.get_facts(ship_day)
        after = await repo.get_facts(on)
        b = {f["fact_key"]: f.get("value_num") for f in before}
        a = {f["fact_key"]: f.get("value_num") for f in after}
        keys = ("biblia_stickiness", "biblia_donations_rub", "club_transitions_from_biblia")
        improved = 0
        for k in keys:
            if b.get(k) is None or a.get(k) is None:
                continue
            if float(a[k]) > float(b[k]):
                improved += 1
        verdict = "positive" if improved >= 2 else ("negative" if improved == 0 else "neutral")
        await repo.add_outcome(
            recommendation_id=int(rec["id"]),
            measured_on=on,
            kpi_before=b,
            kpi_after=a,
            verdict=verdict,
            notes=f"auto measure after {days}d",
        )


async def draft_pr_stub(
    cfg: Config,
    repo: AgencyRepo,
    *,
    run_id: int,
    actions: List[Dict[str, Any]],
) -> Optional[int]:
    if not cfg.ENABLE_DRAFT_PR or not actions:
        return None
    # Local patch suggestion only — no git push unless explicitly extended later
    action = actions[0]
    patch = (
        f"# Draft suggestion for biblia (NOT applied)\n"
        f"# Title: {action.get('title')}\n\n"
        f"{action.get('body')}\n\n"
        f"Evidence:\n{action.get('evidence')}\n"
    )
    biblia_path = Path(cfg.GITHUB_BIBLIA_PATH)
    note = f"repo_path={biblia_path} exists={biblia_path.is_dir()}"
    return await repo.add_draft_pr(
        agent_id=AGENT_ID,
        run_id=run_id,
        recommendation_id=None,
        patch_text=patch + "\n# " + note,
        branch_name="",
        status="draft_local",
    )


async def run_bible_bot_manager(
    *,
    cfg: Config,
    pools: Pools,
    day: Optional[date] = None,
    skip_llm: bool = False,
    bot=None,
) -> Dict[str, Any]:
    assert pools.agency and pools.biblia and pools.club
    repo = AgencyRepo(pools.agency)
    from collectors.windows import yesterday_msk

    target = day or yesterday_msk()
    run_id = await repo.upsert_run(AGENT_ID, target, status="running")
    status = "ok"
    try:
        kpi = await collect_with_history(
            biblia=pools.biblia,
            club=pools.club,
            repo=repo,
            day=target,
        )
        await maybe_measure_shipped(repo, kpi, target)

        cta = await collect_cta_metrics(pools.biblia, repo, target)
        themes = await theme_keyword_stats(pools.biblia, target)
        threads = await sample_dialog_threads(
            pools.biblia, target, limit=cfg.DIALOG_SAMPLE_LIMIT
        )
        threads_blob = format_threads_blob(threads)
        content_corr = await correlate_content_with_kpis(repo, target)

        rag_bits = try_rag_snippet(
            cfg,
            "удержание аудитории библейского бота донаты сообщество клуб",
        )

        memory = await repo.list_recommendations(
            agent_id=AGENT_ID,
            statuses=["proposed", "accepted", "shipped"],
            limit=20,
        )
        handoffs = await repo.pending_handoffs(AGENT_ID, limit=10)
        gaps = await repo.open_gaps(limit=15)

        await repo.put_fact(
            fact_date=target,
            fact_key="dialog_theme_stats",
            source_system="agency",
            value_json=themes,
        )

        context = build_context_blob(
            kpi=kpi,
            memory_recs=memory,
            handoffs=handoffs,
            gaps=gaps,
            threads_blob=threads_blob,
            themes=themes,
            content_corr=content_corr,
            cta=cta,
            rag_bits=rag_bits,
        )

        brief_md = numeric_brief(kpi, gaps, cta)
        panel: Dict[str, Any] = {}
        actions: List[Dict[str, Any]] = []

        if not skip_llm:
            hub = LlmHub(cfg)
            research_q = (
                f"Bible chatbot retention donations funnel to paid community. "
                f"Stickiness={kpi['day']['stickiness']:.3f}, "
                f"donations={kpi['day']['donations_rub']}, "
                f"club_transitions={kpi['day']['club_transitions']}, "
                f"payment_starts={cta.get('payment_start_events')}, "
                f"cta_shows={cta.get('donation_button_shows_day')}. "
                f"Themes today: {themes}. Best practices 2025-2026."
            )
            panel = await run_panel(
                hub,
                repo=repo,
                run_id=run_id,
                agent_id=AGENT_ID,
                context_blob=context,
                research_query=research_q,
            )
            final = panel.get("final") or {}
            if final.get("brief_md"):
                brief_md = numeric_brief(kpi, gaps, cta) + "\n\n" + final["brief_md"]
            actions = list(final.get("actions") or [])[:3]
            for gap in final.get("data_gaps") or []:
                if isinstance(gap, str) and gap.strip():
                    # LLM gaps as human text, not fake keys about missing CTA
                    low = gap.strip().lower()
                    if "cta" in low and (
                        "нет" in low or "отсутств" in low or "не видим" in low
                    ):
                        continue
                    await repo.upsert_data_gap(
                        gap_key=f"llm_{gap.strip()[:60]}",
                        description=gap.strip()[:500],
                        on=target,
                        severity="low",
                    )
            for h in final.get("handoffs") or []:
                to_id = (h.get("to_agent_id") or "").strip()
                if to_id and to_id != AGENT_ID:
                    await repo.add_handoff(
                        from_agent_id=AGENT_ID,
                        to_agent_id=to_id,
                        run_id=run_id,
                        subject=str(h.get("subject") or "handoff")[:300],
                        body=str(h.get("body") or ""),
                        payload=h,
                    )
            if panel.get("degraded"):
                status = "degraded"

            await repo.add_artifact(
                run_id=run_id,
                agent_id=AGENT_ID,
                kind="panel_json",
                title="llm_panel",
                body_json=panel,
            )
        else:
            status = "ok"

        for a in actions:
            body = str(a.get("body") or "")
            if a.get("kpi_impact"):
                body = f"{body}\nВлияние на KPI: {a['kpi_impact']}".strip()
            if a.get("how_to_verify"):
                body = f"{body}\nКак проверить: {a['how_to_verify']}".strip()
            await repo.add_recommendation(
                agent_id=AGENT_ID,
                run_id=run_id,
                created_on=target,
                title=str(a.get("title") or "action")[:300],
                body=body,
                evidence=str(a.get("evidence") or ""),
                target_system=str(a.get("target_system") or "biblia"),
                priority=int(a.get("priority") or 2),
                meta={
                    "kpi_impact": a.get("kpi_impact"),
                    "how_to_verify": a.get("how_to_verify"),
                },
            )

        await draft_pr_stub(cfg, repo, run_id=run_id, actions=actions)

        await repo.add_artifact(
            run_id=run_id,
            agent_id=AGENT_ID,
            kind="brief_md",
            title=f"brief_{target.isoformat()}",
            body_text=brief_md,
            body_json={"kpi": kpi["day"], "cta": cta, "actions": actions},
        )

        # persist brief file
        out_dir = Path(__file__).resolve().parents[2] / "data" / "runs"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{target.isoformat()}_bible_bot_manager.md").write_text(
            brief_md, encoding="utf-8"
        )

        if bot is not None:
            await send_brief(
                bot, cfg, brief_md, repo=repo, run_id=run_id, agent_id=AGENT_ID
            )

        await repo.finish_run(
            run_id,
            status=status,
            meta={
                "actions": len(actions),
                "threads": len(threads),
                "skip_llm": skip_llm,
                "cta": cta,
            },
        )
        return {"run_id": run_id, "status": status, "brief": brief_md, "day": target.isoformat()}
    except Exception as e:
        logger.exception("bible_bot_manager failed")
        await repo.finish_run(run_id, status="failed", error_text=str(e)[:2000])
        raise
