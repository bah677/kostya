"""Статистика запусков бота по deep link /start benefit3."""

from __future__ import annotations

import html as html_mod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

from bot.services.report_exclude import sql_exclude_users
from bot.texts.ru_benefit import START_PARAM_BENEFIT3

if TYPE_CHECKING:
    import asyncpg

_TOUCH_KEY = START_PARAM_BENEFIT3

def _period_filter_sql(period_key: str) -> str:
    if period_key == "yesterday":
        return (
            "AND (at.created_at AT TIME ZONE 'Europe/Moscow')::date = b.yday"
        )
    return (
        "AND (at.created_at AT TIME ZONE 'Europe/Moscow')::date "
        "BETWEEN b.d30_start AND b.yday"
    )


def _period_sql(period_key: str, exclude_at: str) -> str:
    return f"""
WITH bounds AS (
    SELECT
        ((NOW() AT TIME ZONE 'Europe/Moscow')::date - 1) AS yday,
        ((NOW() AT TIME ZONE 'Europe/Moscow')::date - 30) AS d30_start
),
raw_touches AS (
    SELECT at.user_id, at.created_at AS ts
    FROM attribution_touches at, bounds b
    WHERE at.touch_key = '{_TOUCH_KEY}'
      AND at.source_type = 'start'
      {_period_filter_sql(period_key)}
      {exclude_at}
),
first_touch AS (
    SELECT DISTINCT ON (user_id) user_id, ts
    FROM raw_touches
    ORDER BY user_id, ts ASC
),
classified AS (
    SELECT
        ft.user_id,
        ft.ts,
        CASE
            WHEN u.created_at >= ft.ts - INTERVAL '5 minutes'
             AND u.created_at <= ft.ts + INTERVAL '5 minutes'
            THEN 'first'
            ELSE 'repeat'
        END AS segment
    FROM first_touch ft
    JOIN users u ON u.user_id = ft.user_id
),
launch_stats AS (
    SELECT
        COUNT(*)::bigint AS touch_events,
        COUNT(DISTINCT user_id)::bigint AS unique_users
    FROM raw_touches
),
segment_stats AS (
    SELECT
        COUNT(*) FILTER (WHERE segment = 'first')::bigint AS first_time,
        COUNT(*) FILTER (WHERE segment = 'repeat')::bigint AS repeat_users
    FROM classified
),
buyers AS (
    SELECT
        c.segment,
        COUNT(DISTINCT c.user_id)::bigint AS buyers,
        COALESCE(SUM(p.amount_rub), 0)::numeric AS total_rub
    FROM classified c
    JOIN orders o ON o.user_id = c.user_id
        AND o.status = 'paid'
        AND o.paid_at >= c.ts
    JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
    GROUP BY c.segment
),
tariffs AS (
    SELECT
        c.segment,
        COALESCE(t.name, t.type, '—') AS tariff_name,
        COUNT(DISTINCT c.user_id)::bigint AS buyers,
        COALESCE(SUM(p.amount_rub), 0)::numeric AS total_rub
    FROM classified c
    JOIN orders o ON o.user_id = c.user_id
        AND o.status = 'paid'
        AND o.paid_at >= c.ts
    JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
    JOIN tariffs t ON t.id = o.tariff_id
    GROUP BY c.segment, COALESCE(t.name, t.type, '—')
)
SELECT
    (SELECT touch_events FROM launch_stats) AS touch_events,
    (SELECT unique_users FROM launch_stats) AS unique_users,
    (SELECT first_time FROM segment_stats) AS first_time,
    (SELECT repeat_users FROM segment_stats) AS repeat_users,
    COALESCE((SELECT buyers FROM buyers WHERE segment = 'first'), 0) AS first_buyers,
    COALESCE((SELECT total_rub FROM buyers WHERE segment = 'first'), 0) AS first_rub,
    COALESCE((SELECT buyers FROM buyers WHERE segment = 'repeat'), 0) AS repeat_buyers,
    COALESCE((SELECT total_rub FROM buyers WHERE segment = 'repeat'), 0) AS repeat_rub
"""


def _tariffs_sql(period_key: str, exclude_at: str) -> str:
    return f"""
WITH bounds AS (
    SELECT
        ((NOW() AT TIME ZONE 'Europe/Moscow')::date - 1) AS yday,
        ((NOW() AT TIME ZONE 'Europe/Moscow')::date - 30) AS d30_start
),
raw_touches AS (
    SELECT at.user_id, at.created_at AS ts
    FROM attribution_touches at, bounds b
    WHERE at.touch_key = '{_TOUCH_KEY}'
      AND at.source_type = 'start'
      {_period_filter_sql(period_key)}
      {exclude_at}
),
first_touch AS (
    SELECT DISTINCT ON (user_id) user_id, ts
    FROM raw_touches
    ORDER BY user_id, ts ASC
),
classified AS (
    SELECT
        ft.user_id,
        ft.ts,
        CASE
            WHEN u.created_at >= ft.ts - INTERVAL '5 minutes'
             AND u.created_at <= ft.ts + INTERVAL '5 minutes'
            THEN 'first'
            ELSE 'repeat'
        END AS segment
    FROM first_touch ft
    JOIN users u ON u.user_id = ft.user_id
)
SELECT
    c.segment,
    COALESCE(t.name, t.type, '—') AS tariff_name,
    COUNT(DISTINCT c.user_id)::bigint AS buyers,
    COALESCE(SUM(p.amount_rub), 0)::numeric AS total_rub
FROM classified c
JOIN orders o ON o.user_id = c.user_id
    AND o.status = 'paid'
    AND o.paid_at >= c.ts
JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
JOIN tariffs t ON t.id = o.tariff_id
GROUP BY c.segment, COALESCE(t.name, t.type, '—')
ORDER BY c.segment, total_rub DESC, tariff_name
"""


@dataclass(frozen=True)
class Benefit3TariffRow:
    tariff_name: str
    buyers: int
    total_rub: float


@dataclass
class Benefit3PeriodReport:
    period_key: str
    period_label: str
    touch_events: int = 0
    unique_users: int = 0
    first_time: int = 0
    repeat_users: int = 0
    first_buyers: int = 0
    first_rub: float = 0.0
    repeat_buyers: int = 0
    repeat_rub: float = 0.0
    tariffs_first: List[Benefit3TariffRow] = field(default_factory=list)
    tariffs_repeat: List[Benefit3TariffRow] = field(default_factory=list)


@dataclass(frozen=True)
class Benefit3DeeplinkReport:
    yesterday: Benefit3PeriodReport
    days_30: Benefit3PeriodReport


def _fmt_rub(amount: float) -> str:
    return f"{int(round(amount)):,}".replace(",", " ") + " ₽"


async def _collect_period(
    pool: "asyncpg.Pool",
    *,
    period_key: str,
    period_label: str,
    exclude_ids: List[int],
) -> Benefit3PeriodReport:
    ex_sql, _ = sql_exclude_users("at.user_id", start_param=1)
    period_sql = _period_sql(period_key, ex_sql)
    tariff_sql = _tariffs_sql(period_key, ex_sql)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(period_sql, *exclude_ids)
        tariff_rows = await conn.fetch(tariff_sql, *exclude_ids)

    data = dict(row or {})
    report = Benefit3PeriodReport(
        period_key=period_key,
        period_label=period_label,
        touch_events=int(data.get("touch_events") or 0),
        unique_users=int(data.get("unique_users") or 0),
        first_time=int(data.get("first_time") or 0),
        repeat_users=int(data.get("repeat_users") or 0),
        first_buyers=int(data.get("first_buyers") or 0),
        first_rub=float(data.get("first_rub") or 0),
        repeat_buyers=int(data.get("repeat_buyers") or 0),
        repeat_rub=float(data.get("repeat_rub") or 0),
    )
    for tr in tariff_rows or []:
        item = Benefit3TariffRow(
            tariff_name=str(tr["tariff_name"] or "—"),
            buyers=int(tr["buyers"] or 0),
            total_rub=float(tr["total_rub"] or 0),
        )
        if tr["segment"] == "first":
            report.tariffs_first.append(item)
        else:
            report.tariffs_repeat.append(item)
    return report


async def collect_benefit3_deeplink_report(pool: "asyncpg.Pool") -> Benefit3DeeplinkReport:
    _, exclude_ids = sql_exclude_users("at.user_id")
    yesterday = await _collect_period(
        pool,
        period_key="yesterday",
        period_label="вчера",
        exclude_ids=exclude_ids,
    )
    days_30 = await _collect_period(
        pool,
        period_key="30d",
        period_label="30 дней",
        exclude_ids=exclude_ids,
    )
    return Benefit3DeeplinkReport(yesterday=yesterday, days_30=days_30)


def _format_tariff_lines(rows: List[Benefit3TariffRow]) -> str:
    if not rows:
        return "  • нет оплат"
    return "\n".join(
        f"  • {html_mod.escape(r.tariff_name)}: {r.buyers} чел. · {_fmt_rub(r.total_rub)}"
        for r in rows
    )


def _format_period_block(period: Benefit3PeriodReport) -> List[str]:
    launches_note = (
        f"{period.unique_users} чел."
        if period.touch_events == period.unique_users
        else f"{period.unique_users} чел. ({period.touch_events} запусков)"
    )
    lines = [
        f"<b>{html_mod.escape(period.period_label)}</b>",
        f"• Запустили: {launches_note}",
        f"• Впервые: {period.first_time} · Повторно: {period.repeat_users}",
        f"• Купили: {period.first_buyers + period.repeat_buyers} "
        f"(впервые: {period.first_buyers} · {_fmt_rub(period.first_rub)}, "
        f"повторно: {period.repeat_buyers} · {_fmt_rub(period.repeat_rub)})",
        "  <i>Впервые:</i>",
        _format_tariff_lines(period.tariffs_first),
        "  <i>Повторно:</i>",
        _format_tariff_lines(period.tariffs_repeat),
    ]
    return lines


def format_benefit3_deeplink_block(report: Optional[Benefit3DeeplinkReport]) -> str:
    if not report:
        return ""
    lines = [
        "<b>🙏 Deep link benefit3</b>",
        "<i>/start benefit3 — молитва благодарности</i>",
        "",
    ]
    lines.extend(_format_period_block(report.yesterday))
    lines.append("")
    lines.extend(_format_period_block(report.days_30))
    return "\n".join(lines)
