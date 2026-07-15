"""Конверсия покупателей тест-драйва (ТД, тарифы promo_test1week*)."""

from __future__ import annotations

import html as html_mod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bot.services.report_exclude import sql_exclude_users

if TYPE_CHECKING:
    import asyncpg

TD_REPORT_PERIOD_DAYS: tuple[int, ...] = (7, 14, 21, 30, 60, 90, 120)

_TD_COHORT_SQL = """
WITH first_td_ever AS (
    SELECT DISTINCT ON (o.user_id)
        o.user_id,
        o.paid_at AS first_td_at,
        COALESCE(
            NULLIF(p.amount_rub, 0),
            CASE WHEN UPPER(COALESCE(p.currency, 'RUB')) = 'RUB' THEN p.amount ELSE 0 END,
            0
        )::numeric AS td_amount_rub
    FROM orders o
    JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
    JOIN tariffs t ON t.id = o.tariff_id
    WHERE o.status = 'paid'
      AND COALESCE(t.type, '') LIKE 'promo_test1week%'
      {exclude_orders}
    ORDER BY o.user_id, o.paid_at ASC
),
cohort AS (
    SELECT *
    FROM first_td_ever
    WHERE first_td_at >= NOW() - make_interval(days => $1)
),
tagged AS (
    SELECT
        c.user_id,
        c.td_amount_rub,
        EXISTS (
            SELECT 1
            FROM orders o2
            JOIN payments p2 ON p2.order_id = o2.id AND p2.status = 'succeeded'
            JOIN tariffs t2 ON t2.id = o2.tariff_id
            WHERE o2.user_id = c.user_id
              AND o2.status = 'paid'
              AND COALESCE(t2.type, '') = 'base'
              AND o2.paid_at > c.first_td_at
        ) AS renewed,
        EXISTS (
            SELECT 1
            FROM license l
            WHERE l.user_id = c.user_id
              AND l.status = 'active'
              AND l.expires_at > NOW()
        ) AS has_active_license
    FROM cohort c
)
SELECT
    COUNT(*)::bigint AS buyers,
    COALESCE(SUM(td_amount_rub), 0)::numeric AS total_rub,
    COUNT(*) FILTER (WHERE renewed)::bigint AS renewed,
    COUNT(*) FILTER (WHERE NOT renewed AND has_active_license)::bigint AS active_td,
    COUNT(*) FILTER (WHERE NOT renewed AND NOT has_active_license)::bigint AS expired_no_renew
FROM tagged
"""


@dataclass(frozen=True)
class TdConversionReport:
    days: int
    buyers: int
    total_rub: float
    active_td: int
    expired_no_renew: int
    renewed: int

    @property
    def conversion_pct(self) -> float:
        if not self.buyers:
            return 0.0
        return round(100.0 * self.renewed / self.buyers, 1)


async def collect_td_conversion_report(pool: "asyncpg.Pool", days: int) -> TdConversionReport:
    days = max(1, min(int(days), 3650))
    ex_sql, ex_args = sql_exclude_users("o.user_id", start_param=2)
    query = _TD_COHORT_SQL.format(exclude_orders=ex_sql)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, days, *ex_args)
    data = dict(row or {})
    return TdConversionReport(
        days=days,
        buyers=int(data.get("buyers") or 0),
        total_rub=float(data.get("total_rub") or 0),
        active_td=int(data.get("active_td") or 0),
        expired_no_renew=int(data.get("expired_no_renew") or 0),
        renewed=int(data.get("renewed") or 0),
    )


def _fmt_rub(amount: float) -> str:
    return f"{int(round(amount)):,}".replace(",", " ") + " ₽"


def _pct(part: int, whole: int) -> str:
    if not whole:
        return "0%"
    return f"{100.0 * part / whole:.1f}%"


def format_td_conversion_html(report: TdConversionReport) -> str:
    b = report.buyers
    lines = [
        "<b>🚗 Конверсия тест-драйва (ТД)</b>",
        f"<i>Период: первые покупки ТД за последние {report.days} дн.</i>",
        "",
        f"<b>Купили ТД:</b> {b} чел.",
        f"<b>Сумма оплат ТД:</b> {_fmt_rub(report.total_rub)}",
        "",
        (
            "<b>Сейчас активен ТД</b> "
            "(лицензия активна, base ещё не оплачен): "
            f"{report.active_td} чел. ({_pct(report.active_td, b)})"
        ),
        (
            "<b>ТД закончился, не продлили</b> "
            "(нет активной лицензии и нет оплаты base): "
            f"{report.expired_no_renew} чел. ({_pct(report.expired_no_renew, b)})"
        ),
        (
            "<b>Продлили</b> (оплатили base после ТД): "
            f"{report.renewed} чел. ({_pct(report.renewed, b)})"
        ),
        "",
        f"<b>Конверсия ТД → base:</b> {report.conversion_pct}%",
        "",
        (
            "<i>Когорта: пользователи, у которых первая оплата "
            "<code>promo_test1week*</code> попала в выбранный период. "
            "«Не продлили» ≠ «ТД ×N» в /excluded: там только "
            "<code>license.status=expired</code> и чистый профиль ТД; "
            "сюда также попадают просроченные по дате с "
            "<code>status=active</code> и смешанный профиль (ТД + 1 мес).</i>"
        ),
    ]
    return "\n".join(lines)
