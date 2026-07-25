"""Метрики CTA доната/клуба из biblia DB (без «пустых» None в отчёте)."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, Optional, Tuple

import asyncpg

from collectors.windows import day_window
from db.repo import AgencyRepo

logger = logging.getLogger(__name__)


async def _cum_counters(conn) -> Tuple[int, int, int]:
    shows = int(
        await conn.fetchval("SELECT COALESCE(SUM(donation_button), 0) FROM users") or 0
    )
    clicks = int(
        await conn.fetchval(
            "SELECT COALESCE(SUM(donation_button_click), 0) FROM users"
        )
        or 0
    )
    proposals = int(
        await conn.fetchval(
            "SELECT COALESCE(SUM(donation_proposal_count), 0) FROM users"
        )
        or 0
    )
    return shows, clicks, proposals


async def _snapshot_pair(
    conn, day: date
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Снимки за day и day-1: metric_daily_snapshots (новая) или metric_snapshots."""
    cur = prev = None
    for table in ("metric_daily_snapshots", "metric_snapshots"):
        try:
            cur = await conn.fetchrow(
                f"""
                SELECT snapshot_date, donation_buttons_shown, donation_button_clicks,
                       donation_proposals
                FROM {table}
                WHERE snapshot_date = $1
                ORDER BY id DESC LIMIT 1
                """,
                day,
            )
            prev = await conn.fetchrow(
                f"""
                SELECT snapshot_date, donation_buttons_shown, donation_button_clicks,
                       donation_proposals
                FROM {table}
                WHERE snapshot_date = $1
                ORDER BY id DESC LIMIT 1
                """,
                day - timedelta(days=1),
            )
            if cur or prev:
                return (dict(cur) if cur else None, dict(prev) if prev else None)
        except Exception:
            continue
    return (None, None)


async def collect_cta_metrics(
    biblia: asyncpg.Pool,
    repo: AgencyRepo,
    day: date,
) -> Dict[str, Any]:
    """
    Дневные метрики:

    1) interaction_logs за календарный день — всегда считаются:
       payment_start, marathon_open (реальные callback-клики).
    2) Дельта кумулятива users.donation_* :
       a) из biblia.metric_snapshots (day vs day-1), если есть;
       b) иначе из agency.shared_facts (снимки прошлых прогонов).
    """
    w = day_window(day)
    async with biblia.acquire() as conn:
        cum_shows, cum_clicks, cum_proposals = await _cum_counters(conn)
        snap_cur, snap_prev = await _snapshot_pair(conn, day)

        payment_starts = int(
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM interaction_logs
                WHERE event_type = 'payment_start'
                  AND created_at >= $1 AND created_at < $2
                """,
                w.start_utc,
                w.end_utc,
            )
            or 0
        )
        payment_start_users = int(
            await conn.fetchval(
                """
                SELECT COUNT(DISTINCT user_id)
                FROM interaction_logs
                WHERE event_type = 'payment_start'
                  AND created_at >= $1 AND created_at < $2
                  AND user_id IS NOT NULL
                """,
                w.start_utc,
                w.end_utc,
            )
            or 0
        )
        marathon_opens = int(
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM interaction_logs
                WHERE event_type = 'marathon_open'
                  AND created_at >= $1 AND created_at < $2
                """,
                w.start_utc,
                w.end_utc,
            )
            or 0
        )
        # воронка оплаты после payment_start
        payment_amount_events = int(
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM interaction_logs
                WHERE created_at >= $1 AND created_at < $2
                  AND (
                    event_type LIKE 'payment_rub_amount_%'
                    OR event_type LIKE 'payment_usd_amount_%'
                    OR event_type LIKE 'payment_eur_amount_%'
                    OR event_type IN (
                      'payment_rub_custom', 'payment_usd_custom', 'payment_eur_custom'
                    )
                  )
                """,
                w.start_utc,
                w.end_utc,
            )
            or 0
        )

    shows_day: Optional[int] = None
    clicks_day: Optional[int] = None
    proposals_day: Optional[int] = None
    delta_source = "none"

    if snap_cur and snap_prev:
        shows_day = max(
            0,
            int(snap_cur["donation_buttons_shown"])
            - int(snap_prev["donation_buttons_shown"]),
        )
        clicks_day = max(
            0,
            int(snap_cur["donation_button_clicks"])
            - int(snap_prev["donation_button_clicks"]),
        )
        proposals_day = max(
            0,
            int(snap_cur["donation_proposals"])
            - int(snap_prev["donation_proposals"]),
        )
        delta_source = "metric_snapshots"
    else:
        prev = day - timedelta(days=1)
        prev_facts = await repo.get_facts(
            prev,
            keys=[
                "biblia_cta_cum_shows",
                "biblia_cta_cum_clicks",
                "biblia_cta_cum_proposals",
            ],
        )
        prev_by = {f["fact_key"]: f.get("value_num") for f in prev_facts}

        def day_delta(cum: int, key: str) -> Optional[int]:
            if key not in prev_by or prev_by[key] is None:
                return None
            return max(0, int(cum) - int(prev_by[key]))

        shows_day = day_delta(cum_shows, "biblia_cta_cum_shows")
        clicks_day = day_delta(cum_clicks, "biblia_cta_cum_clicks")
        proposals_day = day_delta(cum_proposals, "biblia_cta_cum_proposals")
        if shows_day is not None:
            delta_source = "agency_shared_facts"

    ctr = None
    if shows_day and shows_day > 0 and clicks_day is not None:
        ctr = round(clicks_day / shows_day * 100.0, 2)

    # для отчёта: клики всегда есть из логов; показы — если посчитали дельту
    payload = {
        "donation_button_shows_day": shows_day,
        "donation_button_clicks_day": clicks_day,
        "donation_proposals_day": proposals_day,
        "donation_button_ctr_pct": ctr,
        "payment_start_events": payment_starts,
        "payment_start_users": payment_start_users,
        "payment_amount_events": payment_amount_events,
        "marathon_open_events": marathon_opens,
        "cum_shows": cum_shows,
        "cum_clicks": cum_clicks,
        "cum_proposals": cum_proposals,
        "delta_source": delta_source,
        "metric_snapshots_available": bool(snap_cur and snap_prev),
        "notes": (
            "Дневные клики «Поддержать»: interaction_logs.payment_start. "
            "Дневные показы кнопки: дельта users.donation_button "
            "(metric_snapshots или вчерашний снимок agency). "
            "Кнопка в UI рандом: донат callback ИЛИ club URL; club URL в logs не кликается."
        ),
    }

    await repo.put_fact(
        fact_date=day,
        fact_key="biblia_cta_cum_shows",
        source_system="biblia",
        value_num=float(cum_shows),
        value_json=payload,
    )
    await repo.put_fact(
        fact_date=day,
        fact_key="biblia_cta_cum_clicks",
        source_system="biblia",
        value_num=float(cum_clicks),
        value_json=payload,
    )
    await repo.put_fact(
        fact_date=day,
        fact_key="biblia_cta_cum_proposals",
        source_system="biblia",
        value_num=float(cum_proposals),
        value_json=payload,
    )
    await repo.put_fact(
        fact_date=day,
        fact_key="biblia_cta_engagement",
        source_system="biblia",
        value_num=float(payment_starts),
        value_json=payload,
    )

    await repo.close_data_gap("cta_visibility_unknown")

    if shows_day is None:
        await repo.upsert_data_gap(
            gap_key="cta_shows_no_daily_log",
            description=(
                f"Где: biblia.users.donation_button (только кумулятив={cum_shows}) "
                f"и biblia.metric_snapshots (сейчас пусто). "
                f"Что: нет дневного лога показов кнопки, поэтому «показов/день» "
                f"считается только как дельта снимков. "
                f"Клики за {day} уже есть: payment_start={payment_starts} "
                f"(уников {payment_start_users}). "
                f"Со следующего ночного прогона agency дельта показов появится "
                f"(уже сохранён снимок за {day})."
            ),
            severity="low",
            on=day,
            meta=payload,
        )
    else:
        await repo.close_data_gap("cta_shows_no_daily_log")
        await repo.close_data_gap("cta_daily_delta_warmup")

    return payload
