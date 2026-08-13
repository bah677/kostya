# bot/services/prayer_usage_report.py
"""Расширенная статистика голосовых молитв для /adm (экраны обзор/донаты/аномалии/≥10)."""

from __future__ import annotations

import html as html_mod
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Literal, Optional, Tuple
from zoneinfo import ZoneInfo

from bot.services.biblia_daily_report import _EXCLUDED_STATS_USER_IDS

logger = logging.getLogger(__name__)

_MSK = ZoneInfo("Europe/Moscow")
_EXCL_SQL = ", ".join(str(uid) for uid in _EXCLUDED_STATS_USER_IDS)

#: Оценка себестоимости одной голосовой молитвы (USD).
PRAYER_COST_USD = max(
    0.0, float(os.getenv("PRAYER_COST_USD", "0.2") or "0.2")
)
#: Пауэр-юзер: столько и больше compose за выбранный период.
POWER_USER_MIN_PRAYERS = max(
    2, int(os.getenv("PRAYER_POWER_USER_MIN", "10") or "10")
)
_USD_RUB_FALLBACK = 90.0

PrayerScreen = Literal["ov", "dn", "an", "pw"]
PrayerPeriod = Literal["7", "30", "all"]

SCREEN_TITLES = {
    "ov": "Обзор",
    "dn": "Донаты",
    "an": "Аномалии",
    "pw": f"≥{POWER_USER_MIN_PRAYERS}",
}
PERIOD_LABELS = {
    "7": "7 дней",
    "30": "30 дней",
    "all": "всё время",
}

_COMPOSE_KIND = "request_kind LIKE 'personal_prayer_compose%'"
_INTAKE_KIND = "request_kind = 'personal_prayer_intake'"
_USER_OK = f"user_id NOT IN ({_EXCL_SQL})"
_DON_OK = (
    f"status = 'succeeded' AND order_id IS NULL AND user_id NOT IN ({_EXCL_SQL})"
)


def _msk_now() -> datetime:
    return datetime.now(_MSK)


def _msk_day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=_MSK)


def period_bounds(period: PrayerPeriod) -> Tuple[datetime, datetime, str]:
    now = _msk_now()
    end = now + timedelta(seconds=1)
    today = now.date()
    if period == "7":
        start = _msk_day_start(today - timedelta(days=6))
    elif period == "30":
        start = _msk_day_start(today - timedelta(days=29))
    else:
        start = datetime(2020, 1, 1, tzinfo=_MSK)
    return start, end, PERIOD_LABELS[period]


def _fmt_dt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_MSK)
        else:
            dt = dt.astimezone(_MSK)
        return dt.strftime("%d.%m %H:%M")
    return html_mod.escape(str(value))


def _fmt_money(v: Optional[float]) -> str:
    if v is None:
        return "—"
    x = float(v)
    if abs(x - round(x)) < 0.005:
        return f"{int(round(x)):,}".replace(",", " ")
    return f"{x:,.0f}".replace(",", " ")


def _fmt_usd(v: float) -> str:
    if abs(v - round(v)) < 0.005:
        return f"{int(round(v))}"
    return f"{v:.2f}"


def _pct(num: float, den: float) -> str:
    if den <= 0:
        return "—"
    return f"{100.0 * num / den:.1f}%"


def _user_label(row: Dict[str, Any]) -> str:
    uid = int(row.get("user_id") or 0)
    un = (row.get("username") or "").strip()
    fn = (row.get("first_name") or "").strip()
    base = f"<code>{uid}</code>"
    if un:
        return f"{base} @{html_mod.escape(un)}"
    if fn:
        return f"{base} {html_mod.escape(fn[:24])}"
    return base


@dataclass(frozen=True)
class DonationWindowStats:
    hours: int
    donations: int
    users: int
    rub_sum: float
    other_donations: int
    other_rub: float
    prayer_users: int
    converted_users: int


async def _usd_to_rub_rate() -> Tuple[float, str]:
    """Курс USD→RUB (ЦБ) или fallback."""
    try:
        from bot.payments.currency_converter import CurrencyConverterService

        conv = CurrencyConverterService()
        rate = await conv.get_rate_to_rub("USD", _msk_now().date())
        if rate and float(rate) > 0:
            return float(rate), "ЦБ РФ"
    except Exception as e:
        logger.debug("USD rate via CBR failed: %s", e)
    return _USD_RUB_FALLBACK, f"fallback {_USD_RUB_FALLBACK:.0f}"


async def _fetch_period_donation_totals(
    pool, start: datetime, end: datetime
) -> Dict[str, float]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT
                COUNT(*)::int AS n,
                COALESCE(SUM(
                    COALESCE(
                        amount_rub,
                        CASE WHEN UPPER(currency) = 'RUB' THEN amount ELSE NULL END
                    )
                ), 0)::float AS rub
            FROM payments
            WHERE {_DON_OK}
              AND COALESCE(completed_at, created_at) >= $1
              AND COALESCE(completed_at, created_at) < $2
            """,
            start,
            end,
        )
        after24 = await conn.fetchrow(
            f"""
            WITH prayers AS (
                SELECT user_id, created_at AS prayer_at
                FROM token_usage
                WHERE {_COMPOSE_KIND}
                  AND {_USER_OK}
                  AND created_at >= $1 AND created_at < $2
            )
            SELECT
                COUNT(*)::int AS n,
                COALESCE(SUM(
                    COALESCE(
                        d.amount_rub,
                        CASE WHEN UPPER(d.currency) = 'RUB' THEN d.amount ELSE NULL END
                    )
                ), 0)::float AS rub
            FROM payments d
            WHERE d.status = 'succeeded'
              AND d.order_id IS NULL
              AND d.user_id NOT IN ({_EXCL_SQL})
              AND COALESCE(d.completed_at, d.created_at) >= $1
              AND COALESCE(d.completed_at, d.created_at) < $2
              AND EXISTS (
                  SELECT 1 FROM prayers p
                  WHERE p.user_id = d.user_id
                    AND p.prayer_at <= COALESCE(d.completed_at, d.created_at)
                    AND p.prayer_at
                        > COALESCE(d.completed_at, d.created_at) - INTERVAL '24 hours'
              )
            """,
            start,
            end,
        )
    return {
        "all_n": float((row or {}).get("n") or 0),
        "all_rub": float((row or {}).get("rub") or 0),
        "after24_n": float((after24 or {}).get("n") or 0),
        "after24_rub": float((after24 or {}).get("rub") or 0),
    }


def _economics_lines(
    *,
    generations: int,
    don_all_rub: float,
    don_after24_rub: float,
    usd_rub: float,
    rate_src: str,
) -> List[str]:
    cost_usd = generations * PRAYER_COST_USD
    cost_rub = cost_usd * usd_rub
    after_usd = (don_after24_rub / usd_rub) if usd_rub else 0.0
    all_usd = (don_all_rub / usd_rub) if usd_rub else 0.0
    net_after = don_after24_rub - cost_rub
    net_all = don_all_rub - cost_rub
    cover_after = _pct(don_after24_rub, cost_rub) if cost_rub else "—"
    cover_all = _pct(don_all_rub, cost_rub) if cost_rub else "—"
    return [
        "<b>Экономика</b>",
        (
            f"<i>себестоимость ≈ {_fmt_usd(PRAYER_COST_USD)} $/молитва · "
            f"курс USD {usd_rub:.2f} ₽ ({html_mod.escape(rate_src)})</i>"
        ),
        (
            f"• затраты на {generations} молитв: "
            f"<b>${_fmt_usd(cost_usd)}</b> ≈ <b>{_fmt_money(cost_rub)} ₽</b>"
        ),
        (
            f"• донаты в 24ч после молитвы: "
            f"<b>{_fmt_money(don_after24_rub)} ₽</b> (~${_fmt_usd(after_usd)}) · "
            f"покрытие затрат <b>{cover_after}</b>"
        ),
        (
            f"• все донаты периода: "
            f"<b>{_fmt_money(don_all_rub)} ₽</b> (~${_fmt_usd(all_usd)}) · "
            f"покрытие <b>{cover_all}</b>"
        ),
        (
            f"• баланс (донаты 24ч − затраты): "
            f"<b>{_fmt_money(net_after)} ₽</b>"
            f"{' ✅' if net_after >= 0 else ' ⚠️'}"
        ),
        (
            f"• баланс (все донаты − затраты): "
            f"<b>{_fmt_money(net_all)} ₽</b>"
            f"{' ✅' if net_all >= 0 else ' ⚠️'}"
        ),
    ]


async def _fetch_overview(pool, start: datetime, end: datetime) -> Dict[str, Any]:
    async with pool.acquire() as conn:
        compose = await conn.fetchrow(
            f"""
            SELECT
                COUNT(*)::int AS generations,
                COUNT(DISTINCT user_id)::int AS unique_users,
                MIN(created_at) AS first_at,
                MAX(created_at) AS last_at
            FROM token_usage
            WHERE {_COMPOSE_KIND}
              AND {_USER_OK}
              AND created_at >= $1 AND created_at < $2
            """,
            start,
            end,
        )
        intake = await conn.fetchrow(
            f"""
            SELECT
                COUNT(*)::int AS starts,
                COUNT(DISTINCT user_id)::int AS unique_users
            FROM token_usage
            WHERE {_INTAKE_KIND}
              AND {_USER_OK}
              AND created_at >= $1 AND created_at < $2
            """,
            start,
            end,
        )
        variants = await conn.fetch(
            f"""
            SELECT
                CASE
                  WHEN request_kind = 'personal_prayer_compose_a' THEN 'A'
                  WHEN request_kind = 'personal_prayer_compose_b' THEN 'B'
                  ELSE 'legacy'
                END AS variant,
                COUNT(*)::int AS n,
                COUNT(DISTINCT user_id)::int AS u
            FROM token_usage
            WHERE {_COMPOSE_KIND}
              AND {_USER_OK}
              AND created_at >= $1 AND created_at < $2
            GROUP BY 1
            ORDER BY n DESC
            """,
            start,
            end,
        )
        daily = await conn.fetch(
            f"""
            SELECT (created_at AT TIME ZONE 'Europe/Moscow')::date AS d,
                   COUNT(*)::int AS n
            FROM token_usage
            WHERE {_COMPOSE_KIND}
              AND {_USER_OK}
              AND created_at >= $1 AND created_at < $2
            GROUP BY 1
            ORDER BY 1
            """,
            start,
            end,
        )
        power_n = await conn.fetchval(
            f"""
            SELECT COUNT(*)::int FROM (
                SELECT user_id
                FROM token_usage
                WHERE {_COMPOSE_KIND}
                  AND {_USER_OK}
                  AND created_at >= $1 AND created_at < $2
                GROUP BY user_id
                HAVING COUNT(*) >= $3
            ) t
            """,
            start,
            end,
            int(POWER_USER_MIN_PRAYERS),
        )
    gens = int((compose or {}).get("generations") or 0)
    uniq = int((compose or {}).get("unique_users") or 0)
    starts = int((intake or {}).get("starts") or 0)
    start_u = int((intake or {}).get("unique_users") or 0)
    day_counts = [int(r["n"]) for r in daily]
    peak_n = max(day_counts) if day_counts else 0
    peak_d = None
    if day_counts:
        for r in daily:
            if int(r["n"]) == peak_n:
                peak_d = r["d"]
                break
    median_n = 0.0
    if day_counts:
        s = sorted(day_counts)
        mid = len(s) // 2
        median_n = float(s[mid]) if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0
    today_n = 0
    today = _msk_now().date()
    for r in daily:
        if r["d"] == today:
            today_n = int(r["n"])
            break
    don = await _fetch_period_donation_totals(pool, start, end)
    usd_rub, rate_src = await _usd_to_rub_rate()
    return {
        "generations": gens,
        "unique_users": uniq,
        "avg_per_user": (gens / uniq) if uniq else 0.0,
        "intake_starts": starts,
        "intake_users": start_u,
        "first_at": (compose or {}).get("first_at"),
        "last_at": (compose or {}).get("last_at"),
        "variants": [dict(r) for r in variants],
        "active_days": len(day_counts),
        "peak_n": peak_n,
        "peak_d": peak_d,
        "median_n": median_n,
        "today_n": today_n,
        "power_users": int(power_n or 0),
        "don_all_rub": don["all_rub"],
        "don_after24_rub": don["after24_rub"],
        "usd_rub": usd_rub,
        "rate_src": rate_src,
    }


async def _donation_window(
    pool, start: datetime, end: datetime, hours: int
) -> DonationWindowStats:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            WITH prayers AS (
                SELECT user_id, created_at AS prayer_at
                FROM token_usage
                WHERE {_COMPOSE_KIND}
                  AND {_USER_OK}
                  AND created_at >= $1 AND created_at < $2
            ),
            prayer_users AS (
                SELECT COUNT(DISTINCT user_id)::int AS n FROM prayers
            ),
            dons AS (
                SELECT
                    user_id,
                    COALESCE(completed_at, created_at) AS paid_at,
                    COALESCE(
                        amount_rub,
                        CASE WHEN UPPER(currency) = 'RUB' THEN amount ELSE NULL END
                    ) AS rub
                FROM payments
                WHERE {_DON_OK}
                  AND COALESCE(completed_at, created_at) >= $1
                  AND COALESCE(completed_at, created_at) < $2
            ),
            tagged AS (
                SELECT
                    d.*,
                    EXISTS (
                        SELECT 1 FROM prayers p
                        WHERE p.user_id = d.user_id
                          AND p.prayer_at <= d.paid_at
                          AND p.prayer_at > d.paid_at - make_interval(hours => $3::int)
                    ) AS after_prayer
                FROM dons d
            )
            SELECT
                (SELECT n FROM prayer_users) AS prayer_users,
                COUNT(*) FILTER (WHERE after_prayer)::int AS d_after,
                COUNT(DISTINCT user_id) FILTER (WHERE after_prayer)::int AS u_after,
                COALESCE(SUM(rub) FILTER (WHERE after_prayer), 0)::float AS rub_after,
                COUNT(*) FILTER (WHERE NOT after_prayer)::int AS d_other,
                COALESCE(SUM(rub) FILTER (WHERE NOT after_prayer), 0)::float AS rub_other,
                COUNT(DISTINCT user_id) FILTER (WHERE after_prayer)::int AS converted
            FROM tagged
            """,
            start,
            end,
            int(hours),
        )
    return DonationWindowStats(
        hours=hours,
        donations=int((row or {}).get("d_after") or 0),
        users=int((row or {}).get("u_after") or 0),
        rub_sum=float((row or {}).get("rub_after") or 0),
        other_donations=int((row or {}).get("d_other") or 0),
        other_rub=float((row or {}).get("rub_other") or 0),
        prayer_users=int((row or {}).get("prayer_users") or 0),
        converted_users=int((row or {}).get("converted") or 0),
    )


async def _fetch_anomalies(pool, start: datetime, end: datetime) -> Dict[str, Any]:
    async with pool.acquire() as conn:
        top = await conn.fetch(
            f"""
            SELECT
                t.user_id,
                COUNT(*)::int AS n,
                MIN(t.created_at) AS first_at,
                MAX(t.created_at) AS last_at,
                u.username,
                u.first_name
            FROM token_usage t
            LEFT JOIN users u ON u.user_id = t.user_id
            WHERE t.request_kind LIKE 'personal_prayer_compose%'
              AND t.user_id NOT IN ({_EXCL_SQL})
              AND t.created_at >= $1 AND t.created_at < $2
            GROUP BY t.user_id, u.username, u.first_name
            ORDER BY n DESC, last_at DESC
            LIMIT 10
            """,
            start,
            end,
        )
        bursts = await conn.fetch(
            f"""
            WITH ordered AS (
                SELECT
                    user_id,
                    created_at,
                    COUNT(*) OVER (
                        PARTITION BY user_id
                        ORDER BY created_at
                        RANGE BETWEEN INTERVAL '1 hour' PRECEDING AND CURRENT ROW
                    ) AS in_hour
                FROM token_usage
                WHERE {_COMPOSE_KIND}
                  AND {_USER_OK}
                  AND created_at >= $1 AND created_at < $2
            ),
            peaks AS (
                SELECT user_id, MAX(in_hour)::int AS peak
                FROM ordered
                GROUP BY user_id
                HAVING MAX(in_hour) >= 5
            )
            SELECT
                p.user_id,
                p.peak,
                u.username,
                u.first_name
            FROM peaks p
            LEFT JOIN users u ON u.user_id = p.user_id
            ORDER BY p.peak DESC, p.user_id
            LIMIT 8
            """,
            start,
            end,
        )
        heavy_no = await conn.fetch(
            f"""
            WITH gens AS (
                SELECT user_id, COUNT(*)::int AS n
                FROM token_usage
                WHERE {_COMPOSE_KIND}
                  AND {_USER_OK}
                  AND created_at >= $1 AND created_at < $2
                GROUP BY user_id
                HAVING COUNT(*) >= 5
            ),
            after_don AS (
                SELECT DISTINCT p.user_id
                FROM token_usage p
                JOIN payments d
                  ON d.user_id = p.user_id
                 AND d.status = 'succeeded'
                 AND d.order_id IS NULL
                 AND d.user_id NOT IN ({_EXCL_SQL})
                 AND COALESCE(d.completed_at, d.created_at) >= p.created_at
                 AND COALESCE(d.completed_at, d.created_at)
                     < p.created_at + INTERVAL '24 hours'
                WHERE p.request_kind LIKE 'personal_prayer_compose%'
                  AND p.user_id NOT IN ({_EXCL_SQL})
                  AND p.created_at >= $1 AND p.created_at < $2
            )
            SELECT
                g.user_id,
                g.n,
                u.username,
                u.first_name
            FROM gens g
            LEFT JOIN users u ON u.user_id = g.user_id
            WHERE NOT EXISTS (
                SELECT 1 FROM after_don a WHERE a.user_id = g.user_id
            )
            ORDER BY g.n DESC
            LIMIT 8
            """,
            start,
            end,
        )
        donors = await conn.fetch(
            f"""
            WITH hits AS (
                SELECT
                    d.user_id,
                    COUNT(*)::int AS dons,
                    COALESCE(SUM(
                        COALESCE(
                            d.amount_rub,
                            CASE WHEN UPPER(d.currency) = 'RUB' THEN d.amount ELSE NULL END
                        )
                    ), 0)::float AS rub
                FROM payments d
                WHERE d.status = 'succeeded'
                  AND d.order_id IS NULL
                  AND d.user_id NOT IN ({_EXCL_SQL})
                  AND COALESCE(d.completed_at, d.created_at) >= $1
                  AND COALESCE(d.completed_at, d.created_at) < $2
                  AND EXISTS (
                      SELECT 1 FROM token_usage p
                      WHERE p.request_kind LIKE 'personal_prayer_compose%'
                        AND p.user_id = d.user_id
                        AND p.user_id NOT IN ({_EXCL_SQL})
                        AND p.created_at <= COALESCE(d.completed_at, d.created_at)
                        AND p.created_at
                            > COALESCE(d.completed_at, d.created_at) - INTERVAL '24 hours'
                  )
                GROUP BY d.user_id
            )
            SELECT
                h.user_id,
                h.dons,
                h.rub,
                u.username,
                u.first_name,
                (
                    SELECT COUNT(*)::int FROM token_usage t
                    WHERE t.request_kind LIKE 'personal_prayer_compose%'
                      AND t.user_id = h.user_id
                      AND t.created_at >= $1 AND t.created_at < $2
                ) AS prayers
            FROM hits h
            LEFT JOIN users u ON u.user_id = h.user_id
            ORDER BY h.rub DESC NULLS LAST, h.dons DESC
            LIMIT 8
            """,
            start,
            end,
        )
        spike_days = await conn.fetch(
            f"""
            WITH daily AS (
                SELECT (created_at AT TIME ZONE 'Europe/Moscow')::date AS d,
                       COUNT(*)::int AS n
                FROM token_usage
                WHERE {_COMPOSE_KIND}
                  AND {_USER_OK}
                  AND created_at >= $1 AND created_at < $2
                GROUP BY 1
            ),
            med AS (
                SELECT COALESCE(
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY n),
                    0
                ) AS med_n
                FROM daily
            )
            SELECT d.d, d.n, m.med_n
            FROM daily d
            CROSS JOIN med m
            WHERE m.med_n > 0 AND d.n >= 2 * m.med_n
            ORDER BY d.n DESC
            LIMIT 5
            """,
            start,
            end,
        )
    return {
        "top": [dict(r) for r in top],
        "bursts": [dict(r) for r in bursts],
        "heavy_no_donate": [dict(r) for r in heavy_no],
        "donors_after": [dict(r) for r in donors],
        "spike_days": [dict(r) for r in spike_days],
    }


async def _fetch_power_users(
    pool, start: datetime, end: datetime, *, min_prayers: int
) -> Dict[str, Any]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            WITH daily_counts AS (
                SELECT
                    user_id,
                    (created_at AT TIME ZONE 'Europe/Moscow')::date AS d,
                    COUNT(*)::int AS n
                FROM token_usage
                WHERE {_COMPOSE_KIND}
                  AND {_USER_OK}
                  AND created_at >= $1 AND created_at < $2
                GROUP BY user_id, d
            ),
            gens AS (
                SELECT
                    user_id,
                    SUM(n)::int AS n,
                    MIN(d) AS first_d,
                    MAX(d) AS last_d,
                    COUNT(*)::int AS active_days,
                    MAX(n)::int AS max_day
                FROM daily_counts
                GROUP BY user_id
                HAVING SUM(n) >= $3
            ),
            first_last AS (
                SELECT
                    t.user_id,
                    MIN(t.created_at) AS first_at,
                    MAX(t.created_at) AS last_at
                FROM token_usage t
                JOIN gens g ON g.user_id = t.user_id
                WHERE t.request_kind LIKE 'personal_prayer_compose%'
                  AND t.created_at >= $1 AND t.created_at < $2
                GROUP BY t.user_id
            ),
            bursts AS (
                SELECT user_id, MAX(in_hour)::int AS peak_hour
                FROM (
                    SELECT
                        user_id,
                        COUNT(*) OVER (
                            PARTITION BY user_id
                            ORDER BY created_at
                            RANGE BETWEEN INTERVAL '1 hour' PRECEDING AND CURRENT ROW
                        ) AS in_hour
                    FROM token_usage
                    WHERE {_COMPOSE_KIND}
                      AND {_USER_OK}
                      AND created_at >= $1 AND created_at < $2
                ) o
                GROUP BY user_id
            ),
            dons_period AS (
                SELECT
                    user_id,
                    COUNT(*)::int AS dons,
                    COALESCE(SUM(
                        COALESCE(
                            amount_rub,
                            CASE WHEN UPPER(currency) = 'RUB' THEN amount ELSE NULL END
                        )
                    ), 0)::float AS rub
                FROM payments
                WHERE {_DON_OK}
                  AND COALESCE(completed_at, created_at) >= $1
                  AND COALESCE(completed_at, created_at) < $2
                GROUP BY user_id
            ),
            dons_after AS (
                SELECT
                    d.user_id,
                    COUNT(*)::int AS dons,
                    COALESCE(SUM(
                        COALESCE(
                            d.amount_rub,
                            CASE WHEN UPPER(d.currency) = 'RUB' THEN d.amount ELSE NULL END
                        )
                    ), 0)::float AS rub
                FROM payments d
                WHERE d.status = 'succeeded'
                  AND d.order_id IS NULL
                  AND d.user_id NOT IN ({_EXCL_SQL})
                  AND COALESCE(d.completed_at, d.created_at) >= $1
                  AND COALESCE(d.completed_at, d.created_at) < $2
                  AND EXISTS (
                      SELECT 1 FROM token_usage p
                      WHERE p.request_kind LIKE 'personal_prayer_compose%'
                        AND p.user_id = d.user_id
                        AND p.created_at <= COALESCE(d.completed_at, d.created_at)
                        AND p.created_at
                            > COALESCE(d.completed_at, d.created_at) - INTERVAL '24 hours'
                        AND p.created_at >= $1 AND p.created_at < $2
                  )
                GROUP BY d.user_id
            )
            SELECT
                g.user_id,
                g.n,
                fl.first_at,
                fl.last_at,
                g.active_days,
                g.max_day,
                COALESCE(b.peak_hour, 0)::int AS peak_hour,
                COALESCE(dp.dons, 0)::int AS dons_period,
                COALESCE(dp.rub, 0)::float AS rub_period,
                COALESCE(da.dons, 0)::int AS dons_after24,
                COALESCE(da.rub, 0)::float AS rub_after24,
                u.username,
                u.first_name
            FROM gens g
            JOIN first_last fl ON fl.user_id = g.user_id
            LEFT JOIN bursts b ON b.user_id = g.user_id
            LEFT JOIN dons_period dp ON dp.user_id = g.user_id
            LEFT JOIN dons_after da ON da.user_id = g.user_id
            LEFT JOIN users u ON u.user_id = g.user_id
            ORDER BY g.n DESC, fl.last_at DESC
            """,
            start,
            end,
            int(min_prayers),
        )
    usd_rub, rate_src = await _usd_to_rub_rate()
    users = [dict(r) for r in rows]
    total_prayers = sum(int(u["n"]) for u in users)
    return {
        "users": users,
        "min_prayers": min_prayers,
        "total_prayers": total_prayers,
        "usd_rub": usd_rub,
        "rate_src": rate_src,
    }


def _header(screen: PrayerScreen, period_label: str, now: datetime) -> List[str]:
    now_s = now.strftime("%d.%m.%Y %H:%M MSK")
    excl = ", ".join(str(uid) for uid in _EXCLUDED_STATS_USER_IDS)
    title = SCREEN_TITLES.get(screen, screen)
    if screen == "pw":
        title = f"Пауэры (≥{POWER_USER_MIN_PRAYERS})"
    return [
        f"<b>🙏 Молитвы — {title}</b>",
        f"<i>период: {html_mod.escape(period_label)} · {html_mod.escape(now_s)}</i>",
        f"<i>без учёта {html_mod.escape(excl)}</i>",
        "",
    ]


def format_overview(data: Dict[str, Any], period_label: str) -> str:
    parts = _header("ov", period_label, _msk_now())
    gens = int(data["generations"])
    uniq = int(data["unique_users"])
    avg = float(data["avg_per_user"])
    parts.extend(
        [
            f"• генераций: <b>{gens}</b>",
            f"• уников: <b>{uniq}</b> · ср. на человека: <b>{avg:.1f}</b>",
            (
                f"• анкета (intake): <b>{int(data['intake_starts'])}</b> стартов / "
                f"<b>{int(data['intake_users'])}</b> уников"
            ),
            (
                f"• доходимость compose/intake (старты): "
                f"<b>{_pct(gens, float(data['intake_starts']))}</b>"
            ),
            (
                f"• пауэры (≥{POWER_USER_MIN_PRAYERS} молитв): "
                f"<b>{int(data.get('power_users') or 0)}</b> чел. "
                f"<i>(экран «≥{POWER_USER_MIN_PRAYERS}»)</i>"
            ),
            f"• первая: {_fmt_dt(data.get('first_at'))} · последняя: {_fmt_dt(data.get('last_at'))}",
            "",
            "<b>По дням</b>",
            f"• активных дней: <b>{int(data['active_days'])}</b>",
            (
                f"• пик: <b>{int(data['peak_n'])}</b>"
                + (
                    f" ({data['peak_d'].strftime('%d.%m')})"
                    if data.get("peak_d")
                    else ""
                )
            ),
            f"• медиана/день: <b>{float(data['median_n']):.0f}</b> · сегодня: <b>{int(data['today_n'])}</b>",
        ]
    )
    variants = data.get("variants") or []
    if variants:
        parts.append("")
        parts.append("<b>Варианты compose</b>")
        for v in variants:
            parts.append(
                f"• {html_mod.escape(str(v['variant']))}: "
                f"<b>{int(v['n'])}</b> / {int(v['u'])} уник."
            )
    parts.append("")
    parts.extend(
        _economics_lines(
            generations=gens,
            don_all_rub=float(data.get("don_all_rub") or 0),
            don_after24_rub=float(data.get("don_after24_rub") or 0),
            usd_rub=float(data.get("usd_rub") or _USD_RUB_FALLBACK),
            rate_src=str(data.get("rate_src") or "fallback"),
        )
    )
    return "\n".join(parts)


def format_donations(
    windows: List[DonationWindowStats],
    period_label: str,
    *,
    generations: int,
    usd_rub: float,
    rate_src: str,
) -> str:
    parts = _header("dn", period_label, _msk_now())
    w24 = next((w for w in windows if w.hours == 24), None)
    don_after = float(w24.rub_sum) if w24 else 0.0
    don_all = float((w24.rub_sum + w24.other_rub) if w24 else 0.0)
    parts.extend(
        _economics_lines(
            generations=generations,
            don_all_rub=don_all,
            don_after24_rub=don_after,
            usd_rub=usd_rub,
            rate_src=rate_src,
        )
    )
    parts.append("")
    parts.append(
        "<i>«После молитвы» = успешный донат в окне после compose того же user_id. "
        "Это корреляция по времени, не доказанная причинность.</i>"
    )
    parts.append("")
    for w in windows:
        total_d = w.donations + w.other_donations
        total_rub = w.rub_sum + w.other_rub
        parts.extend(
            [
                f"<b>Окно {w.hours} ч</b>",
                (
                    f"• после молитвы: <b>{w.donations}</b> дон. / "
                    f"<b>{w.users}</b> чел. / {_fmt_money(w.rub_sum)} ₽"
                ),
                (
                    f"• остальные донаты периода: <b>{w.other_donations}</b> / "
                    f"{_fmt_money(w.other_rub)} ₽"
                ),
                (
                    f"• доля донатов после молитвы: "
                    f"<b>{_pct(w.donations, float(total_d))}</b>"
                    f" · по ₽: <b>{_pct(w.rub_sum, total_rub)}</b>"
                ),
                (
                    f"• конверсия уников с молитвой → донат в окне: "
                    f"<b>{w.converted_users}</b> / {w.prayer_users} "
                    f"({_pct(w.converted_users, float(w.prayer_users))})"
                ),
                "",
            ]
        )
    return "\n".join(parts).rstrip()


def format_anomalies(data: Dict[str, Any], period_label: str) -> str:
    parts = _header("an", period_label, _msk_now())

    parts.append("<b>Топ по генерациям</b>")
    top = data.get("top") or []
    if not top:
        parts.append("• нет данных")
    else:
        for i, r in enumerate(top, 1):
            parts.append(
                f"{i}. {_user_label(r)} — <b>{int(r['n'])}</b> "
                f"({_fmt_dt(r.get('first_at'))}→{_fmt_dt(r.get('last_at'))})"
            )

    parts.append("")
    parts.append("<b>Всплески (≥5 за час)</b>")
    bursts = data.get("bursts") or []
    if not bursts:
        parts.append("• нет")
    else:
        for r in bursts:
            parts.append(f"• {_user_label(r)} — пик <b>{int(r['peak'])}</b>/час")

    parts.append("")
    parts.append("<b>Много молитв (≥5), 0 донатов в 24ч после</b>")
    heavy = data.get("heavy_no_donate") or []
    if not heavy:
        parts.append("• нет")
    else:
        for r in heavy:
            parts.append(f"• {_user_label(r)} — <b>{int(r['n'])}</b> молитв")

    parts.append("")
    parts.append("<b>Донатили в 24ч после молитвы</b>")
    donors = data.get("donors_after") or []
    if not donors:
        parts.append("• нет")
    else:
        for r in donors:
            parts.append(
                f"• {_user_label(r)} — дон. <b>{int(r['dons'])}</b> / "
                f"{_fmt_money(r.get('rub'))} ₽ · молитв {int(r.get('prayers') or 0)}"
            )

    spikes = data.get("spike_days") or []
    if spikes:
        parts.append("")
        parts.append("<b>Дни ≫ медианы (≥2×)</b>")
        for r in spikes:
            d = r.get("d")
            ds = d.strftime("%d.%m") if hasattr(d, "strftime") else str(d)
            parts.append(
                f"• {ds}: <b>{int(r['n'])}</b> (мед. {float(r['med_n']):.0f})"
            )

    parts.append("")
    parts.append(
        f"<i>Детали по ≥{POWER_USER_MIN_PRAYERS} молитв — экран «≥{POWER_USER_MIN_PRAYERS}».</i>"
    )
    return "\n".join(parts)


def format_power_users(data: Dict[str, Any], period_label: str) -> str:
    parts = _header("pw", period_label, _msk_now())
    users = data.get("users") or []
    min_n = int(data.get("min_prayers") or POWER_USER_MIN_PRAYERS)
    usd_rub = float(data.get("usd_rub") or _USD_RUB_FALLBACK)
    rate_src = str(data.get("rate_src") or "fallback")
    total_prayers = int(data.get("total_prayers") or 0)
    cost_usd = total_prayers * PRAYER_COST_USD
    cost_rub = cost_usd * usd_rub
    rub_after = sum(float(u.get("rub_after24") or 0) for u in users)
    rub_period = sum(float(u.get("rub_period") or 0) for u in users)

    parts.extend(
        [
            f"• порог: ≥<b>{min_n}</b> compose · найдено <b>{len(users)}</b> чел.",
            (
                f"• их молитв: <b>{total_prayers}</b> · "
                f"затраты ≈ <b>${_fmt_usd(cost_usd)}</b> "
                f"({_fmt_money(cost_rub)} ₽, курс {usd_rub:.2f} / {html_mod.escape(rate_src)})"
            ),
            (
                f"• их донаты в 24ч после: <b>{_fmt_money(rub_after)} ₽</b> · "
                f"все их донаты периода: <b>{_fmt_money(rub_period)} ₽</b>"
            ),
            (
                f"• баланс группы (24ч − затраты): "
                f"<b>{_fmt_money(rub_after - cost_rub)} ₽</b>"
            ),
            "",
        ]
    )
    if not users:
        parts.append("Никого с таким порогом за период.")
        return "\n".join(parts)

    for i, r in enumerate(users, 1):
        n = int(r["n"])
        u_cost_usd = n * PRAYER_COST_USD
        u_cost_rub = u_cost_usd * usd_rub
        rub_a = float(r.get("rub_after24") or 0)
        rub_p = float(r.get("rub_period") or 0)
        net = rub_a - u_cost_rub
        avg_day = n / max(1, int(r.get("active_days") or 1))
        parts.extend(
            [
                f"<b>{i}. {_user_label(r)}</b> — <b>{n}</b> молитв",
                (
                    f"   {_fmt_dt(r.get('first_at'))}→{_fmt_dt(r.get('last_at'))} · "
                    f"дней {int(r.get('active_days') or 0)} · "
                    f"ср. {avg_day:.1f}/день · "
                    f"макс/день {int(r.get('max_day') or 0)} · "
                    f"пик/час {int(r.get('peak_hour') or 0)}"
                ),
                (
                    f"   донаты: период {int(r.get('dons_period') or 0)} / "
                    f"{_fmt_money(rub_p)} ₽ · "
                    f"в 24ч после {int(r.get('dons_after24') or 0)} / "
                    f"{_fmt_money(rub_a)} ₽"
                ),
                (
                    f"   затраты ≈ ${_fmt_usd(u_cost_usd)} ({_fmt_money(u_cost_rub)} ₽) · "
                    f"баланс 24ч−затраты: <b>{_fmt_money(net)} ₽</b>"
                    f"{' ✅' if net >= 0 else ' ⚠️'}"
                ),
                "",
            ]
        )
    return "\n".join(parts).rstrip()


async def build_prayer_stats_html(
    pool,
    *,
    screen: PrayerScreen = "ov",
    period: PrayerPeriod = "30",
) -> str:
    start, end, label = period_bounds(period)
    try:
        if screen == "ov":
            data = await _fetch_overview(pool, start, end)
            return format_overview(data, label)
        if screen == "dn":
            windows = [
                await _donation_window(pool, start, end, h) for h in (1, 6, 24)
            ]
            async with pool.acquire() as conn:
                gens = int(
                    await conn.fetchval(
                        f"""
                        SELECT COUNT(*)::int
                        FROM token_usage
                        WHERE {_COMPOSE_KIND}
                          AND {_USER_OK}
                          AND created_at >= $1 AND created_at < $2
                        """,
                        start,
                        end,
                    )
                    or 0
                )
            usd_rub, rate_src = await _usd_to_rub_rate()
            return format_donations(
                windows,
                label,
                generations=gens,
                usd_rub=usd_rub,
                rate_src=rate_src,
            )
        if screen == "pw":
            data = await _fetch_power_users(
                pool, start, end, min_prayers=POWER_USER_MIN_PRAYERS
            )
            return format_power_users(data, label)
        data = await _fetch_anomalies(pool, start, end)
        return format_anomalies(data, label)
    except Exception as e:
        logger.exception("prayer stats failed screen=%s period=%s: %s", screen, period, e)
        return (
            f"<b>🙏 Молитвы — {SCREEN_TITLES.get(screen, screen)}</b>\n"
            f"❌ Ошибка отчёта: {html_mod.escape(str(e)[:200])}"
        )


async def build_prayer_usage_report_html(pool) -> str:
    """Совместимость: прежняя кнопка → обзор за 30 дней."""
    return await build_prayer_stats_html(pool, screen="ov", period="30")
