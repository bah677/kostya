"""KPI collectors: biblia stickiness + donations, club transitions."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, Optional

import asyncpg

from collectors.windows import day_window
from db.repo import AgencyRepo

logger = logging.getLogger(__name__)

_USER_MSG_FILTER = """
(
  m.role = 'user'
  OR (COALESCE(m.sender_type, '') = 'user' AND COALESCE(m.role, '') NOT IN ('assistant', 'bot'))
)
"""


async def collect_biblia_kpis(
    biblia: asyncpg.Pool,
    repo: AgencyRepo,
    day: date,
) -> Dict[str, Any]:
    w = day_window(day)
    async with biblia.acquire() as conn:
        dau = int(
            await conn.fetchval(
                f"""
                SELECT COUNT(DISTINCT m.user_id)
                FROM messages m
                WHERE {_USER_MSG_FILTER}
                  AND m.created_at >= $1 AND m.created_at < $2
                """,
                w.start_utc,
                w.end_utc,
            )
            or 0
        )
        mau = int(
            await conn.fetchval(
                f"""
                SELECT COUNT(DISTINCT m.user_id)
                FROM messages m
                WHERE {_USER_MSG_FILTER}
                  AND m.created_at >= $1 AND m.created_at < $2
                """,
                w.mau_start_utc,
                w.end_utc,
            )
            or 0
        )
        donations = float(
            await conn.fetchval(
                """
                SELECT COALESCE(SUM(amount_rub), 0)
                FROM payments
                WHERE status = 'succeeded'
                  AND order_id IS NULL
                  AND amount_rub IS NOT NULL
                  AND created_at >= $1 AND created_at < $2
                """,
                w.start_utc,
                w.end_utc,
            )
            or 0
        )
        donations_cnt = int(
            await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM payments
                WHERE status = 'succeeded'
                  AND order_id IS NULL
                  AND amount_rub IS NOT NULL
                  AND created_at >= $1 AND created_at < $2
                """,
                w.start_utc,
                w.end_utc,
            )
            or 0
        )

    stickiness = (dau / mau) if mau else 0.0
    payload = {
        "dau": dau,
        "mau": mau,
        "stickiness": round(stickiness, 6),
        "donations_rub": round(donations, 2),
        "donations_count": donations_cnt,
    }
    await repo.put_fact(
        fact_date=day,
        fact_key="biblia_dau",
        source_system="biblia",
        value_num=float(dau),
        value_json=payload,
    )
    await repo.put_fact(
        fact_date=day,
        fact_key="biblia_mau",
        source_system="biblia",
        value_num=float(mau),
        value_json=payload,
    )
    await repo.put_fact(
        fact_date=day,
        fact_key="biblia_stickiness",
        source_system="biblia",
        value_num=stickiness,
        value_json=payload,
    )
    await repo.put_fact(
        fact_date=day,
        fact_key="biblia_donations_rub",
        source_system="biblia",
        value_num=float(donations),
        value_json=payload,
    )
    return payload


async def collect_club_transitions(
    club: asyncpg.Pool,
    repo: AgencyRepo,
    day: date,
) -> Dict[str, Any]:
    """
    Уники с атрибуцией biblia→club за день.

    В club `ref_keys.type` / `channel_type` historically = «Библия Бот»
    (не snake_case biblia_bot). Учитываем оба варианта.
    """
    w = day_window(day)
    biblia_types = ("biblia_bot", "Библия Бот", "biblia", "Библия бот")
    async with club.acquire() as conn:
        tagged = int(
            await conn.fetchval(
                """
                SELECT COUNT(DISTINCT at.user_id)
                FROM attribution_touches at
                WHERE at.source_type = 'start'
                  AND at.created_at >= $1 AND at.created_at < $2
                  AND (
                    COALESCE(at.channel_type, '') = ANY($3::text[])
                    OR EXISTS (
                        SELECT 1 FROM ref_keys rk
                        WHERE rk.ref_key = at.ref_key
                          AND rk.type = ANY($3::text[])
                    )
                  )
                """,
                w.start_utc,
                w.end_utc,
                list(biblia_types),
            )
            or 0
        )
        benefit3 = int(
            await conn.fetchval(
                """
                SELECT COUNT(DISTINCT user_id)
                FROM attribution_touches
                WHERE source_type = 'start'
                  AND touch_key = 'benefit3'
                  AND created_at >= $1 AND created_at < $2
                """,
                w.start_utc,
                w.end_utc,
            )
            or 0
        )
        promo_like = int(
            await conn.fetchval(
                """
                SELECT COUNT(DISTINCT user_id)
                FROM attribution_touches
                WHERE source_type = 'start'
                  AND touch_key LIKE 'promo_%'
                  AND COALESCE(channel_type, '') = ''
                  AND created_at >= $1 AND created_at < $2
                """,
                w.start_utc,
                w.end_utc,
            )
            or 0
        )
        total_starts = int(
            await conn.fetchval(
                """
                SELECT COUNT(DISTINCT user_id)
                FROM attribution_touches
                WHERE source_type = 'start'
                  AND created_at >= $1 AND created_at < $2
                """,
                w.start_utc,
                w.end_utc,
            )
            or 0
        )
        last_touch_at = await conn.fetchval(
            "SELECT MAX(created_at) FROM attribution_touches"
        )

    payload = {
        "club_transitions_biblia_tagged": tagged,
        "benefit3_starts": benefit3,
        "promo_untagged_starts": promo_like,
        "all_start_uniques": total_starts,
        "kpi_used": tagged,
        "channel_type_aliases": list(biblia_types),
        "last_attribution_touch_at": str(last_touch_at) if last_touch_at else None,
        "coverage_note": (
            "KPI3 = unique starts with channel/ref type «Библия Бот»/biblia_bot. "
            "benefit3/promo_* without channel_type may be undercounted."
        ),
    }
    await repo.put_fact(
        fact_date=day,
        fact_key="club_transitions_from_biblia",
        source_system="club",
        value_num=float(tagged),
        value_json=payload,
    )
    if benefit3 or promo_like:
        await repo.upsert_data_gap(
            gap_key="kpi3_untyped_start_payloads",
            description=(
                "Часть переходов (benefit3 / promo_* без channel_type) "
                "не входит в канонический KPI3."
            ),
            severity="medium",
            on=day,
            meta=payload,
        )
    # если атрибуция давно не пишется — критичный пробел
    if last_touch_at is not None:
        from datetime import datetime, timezone

        age_days = (datetime.now(timezone.utc) - last_touch_at).days
        if age_days > 7:
            await repo.upsert_data_gap(
                gap_key="attribution_touches_stale",
                description=(
                    f"Последний attribution_touch {last_touch_at.isoformat()} "
                    f"({age_days} дн. назад). KPI3 по свежим дням будет 0 — "
                    "проверить запись /start в club onboarding."
                ),
                severity="high",
                on=day,
                meta={"last_touch_at": str(last_touch_at), "age_days": age_days},
            )
    elif total_starts == 0:
        await repo.upsert_data_gap(
            gap_key="attribution_touches_empty_day",
            description="За день нет source_type=start в attribution_touches.",
            severity="medium",
            on=day,
        )
    return payload


async def kpi_bundle_for_day(
    repo: AgencyRepo, day: date
) -> Dict[str, Any]:
    facts = await repo.get_facts(day)
    by_key = {f["fact_key"]: f for f in facts}
    out: Dict[str, Any] = {"day": day.isoformat()}
    for k in (
        "biblia_dau",
        "biblia_mau",
        "biblia_stickiness",
        "biblia_donations_rub",
        "club_transitions_from_biblia",
    ):
        f = by_key.get(k)
        out[k] = f["value_num"] if f else None
        if f and f.get("value_json"):
            out[f"{k}_meta"] = f["value_json"]
    return out


async def collect_day(
    *,
    biblia: asyncpg.Pool,
    club: asyncpg.Pool,
    repo: AgencyRepo,
    day: date,
) -> Dict[str, Any]:
    biblia_k = await collect_biblia_kpis(biblia, repo, day)
    club_k = await collect_club_transitions(club, repo, day)
    stick = biblia_k["stickiness"]
    return {
        "day": day.isoformat(),
        "dau": biblia_k["dau"],
        "mau": biblia_k["mau"],
        "stickiness": stick,
        "donations_rub": biblia_k["donations_rub"],
        "club_transitions": club_k["kpi_used"],
        "club_meta": club_k,
    }


async def collect_with_history(
    *,
    biblia: asyncpg.Pool,
    club: asyncpg.Pool,
    repo: AgencyRepo,
    day: date,
    history_days: int = 30,
) -> Dict[str, Any]:
    """Collect target day + ensure prev day exists; return comparison slice."""
    today_kpi = await collect_day(biblia=biblia, club=club, repo=repo, day=day)
    prev = day - timedelta(days=1)
    # prev may already be in facts; refresh cheaply
    prev_kpi = await collect_day(biblia=biblia, club=club, repo=repo, day=prev)

    since = day - timedelta(days=history_days)
    hist = {}
    for key, src in (
        ("biblia_stickiness", "biblia"),
        ("biblia_donations_rub", "biblia"),
        ("club_transitions_from_biblia", "club"),
        ("biblia_dau", "biblia"),
    ):
        hist[key] = await repo.get_fact_history(key, src, since, day)

    def _avg(rows, n=7):
        vals = [float(r["value_num"]) for r in rows[-n:] if r.get("value_num") is not None]
        return sum(vals) / len(vals) if vals else None

    return {
        "day": today_kpi,
        "prev_day": prev_kpi,
        "avg7": {
            "stickiness": _avg(hist["biblia_stickiness"], 7),
            "donations_rub": _avg(hist["biblia_donations_rub"], 7),
            "club_transitions": _avg(hist["club_transitions_from_biblia"], 7),
            "dau": _avg(hist["biblia_dau"], 7),
        },
        "history": hist,
    }
