"""Слабая корреляция content_events ↔ KPI + импорт-заглушка из RAG meta."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from collectors.windows import MSK, day_window
from db.repo import AgencyRepo

logger = logging.getLogger(__name__)


async def correlate_content_with_kpis(
    repo: AgencyRepo,
    day: date,
    *,
    window_days: int = 2,
) -> Dict[str, Any]:
    """
    Берёт content_events около дня и сравнивает KPI дня с avg7.
    Это weak correlation, не каузация.
    """
    center = datetime.combine(day, datetime.min.time(), tzinfo=MSK)
    events = await repo.content_events_around(center, days=window_days, limit=30)
    facts = await repo.get_facts(day)
    by = {f["fact_key"]: f.get("value_num") for f in facts}

    since = day - timedelta(days=7)
    hist_dau = await repo.get_fact_history("biblia_dau", "biblia", since, day)
    hist_don = await repo.get_fact_history("biblia_donations_rub", "biblia", since, day)
    hist_tr = await repo.get_fact_history(
        "club_transitions_from_biblia", "club", since, day
    )

    def avg(rows):
        vals = [float(r["value_num"]) for r in rows[:-1] if r.get("value_num") is not None]
        return sum(vals) / len(vals) if vals else None

    a_dau, a_don, a_tr = avg(hist_dau), avg(hist_don), avg(hist_tr)
    dau = by.get("biblia_dau")
    don = by.get("biblia_donations_rub")
    tr = by.get("club_transitions_from_biblia")

    def delta(cur, base):
        if cur is None or base is None or base == 0:
            return None
        return round((float(cur) - float(base)) / float(base) * 100.0, 1)

    summary = {
        "events_count": len(events),
        "events": [
            {
                "platform": e.get("platform"),
                "title": e.get("title"),
                "url": e.get("url"),
                "published_at": str(e.get("published_at")),
                "ref_key": e.get("ref_key"),
            }
            for e in events[:10]
        ],
        "kpi_vs_avg7_pct": {
            "dau": delta(dau, a_dau),
            "donations": delta(don, a_don),
            "club_transitions": delta(tr, a_tr),
        },
        "note": (
            "Слабая корреляция: окно ±N дней вокруг постов. "
            "Точная атрибуция только при заполненном ref_key."
        ),
    }
    if not events:
        # не пугаем как «баг KPI» — это опциональный слой соцсетей
        await repo.upsert_data_gap(
            gap_key="content_events_empty",
            description=(
                "Где: agency.content_events (таблица агентства, не biblia/club). "
                "Что: нет записей о постах Кости в соцсетях около этого дня. "
                "Зачем: без этого нельзя связать конкретный пост с DAU/донаты/переходами. "
                "На 3 основных KPI не влияет. "
                "Что сделать: /content_add позже или импорт из RAG; пока агент работает без этого."
            ),
            severity="low",
            on=day,
        )
    else:
        await repo.close_data_gap("content_events_empty")
    return summary
