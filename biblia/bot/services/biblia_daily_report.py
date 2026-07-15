"""Сбор и форматирование ежедневного отчёта Biblia (порт legacy Adm daily_report)."""

from __future__ import annotations

import html
import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from bot.services.metrics_snapshot_storage import MetricsSnapshotStorage

logger = logging.getLogger(__name__)

_MSK = ZoneInfo("Europe/Moscow")
_EXCLUDED_REFERRER_ID = 367302291
_EXCLUDED_DEPTH_USER_ID = 7135176398
# Тестовые/служебные аккаунты — не в метриках донатеров и реферального дерева.
_EXCLUDED_STATS_USER_IDS = (304631563, _EXCLUDED_REFERRER_ID)
_EXCLUDED_DONOR_USER_IDS = _EXCLUDED_STATS_USER_IDS
_EXCLUDED_DONORS_FILTER = (
    f"AND user_id NOT IN ({', '.join(str(uid) for uid in _EXCLUDED_DONOR_USER_IDS)})"
)
_EXCLUDED_REFERRALS_FILTER = f"""
  AND referrer_id::text IS DISTINCT FROM referred_id::text
  AND referrer_id NOT IN ({', '.join(str(uid) for uid in _EXCLUDED_STATS_USER_IDS)})
  AND referred_id NOT IN ({', '.join(f"'{uid}'" for uid in _EXCLUDED_STATS_USER_IDS)})
"""
_REFERRAL_TREE_MAX_DEPTH = 15

_USER_MSG_FILTER = """
(
  m.role = 'user'
  OR (COALESCE(m.sender_type, '') = 'user' AND COALESCE(m.role, '') NOT IN ('assistant', 'bot'))
)
"""

_VOICE_FILTER = """
(
  m.content = '[voice]'
  OR COALESCE(m.message_type, '') IN ('voice', 'audio')
)
"""

_NOT_VOICE_FILTER = f"NOT ({_VOICE_FILTER})"


def _msk_day_bounds(report_day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(report_day, time.min, tzinfo=_MSK)
    return start, start + timedelta(days=1)


def _msk_month_start(month_day: date) -> datetime:
    return datetime.combine(month_day.replace(day=1), time.min, tzinfo=_MSK)


def _msk_month_end_exclusive(month_day: date) -> datetime:
    first = month_day.replace(day=1)
    if first.month == 12:
        next_month = date(first.year + 1, 1, 1)
    else:
        next_month = date(first.year, first.month + 1, 1)
    return datetime.combine(next_month, time.min, tzinfo=_MSK)


def _month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def _format_median_questions(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}"


def _format_median_questions_by_month(
    rows: list[dict[str, Any]],
) -> str:
    if not rows:
        return ""
    lines = [
        f"      – {row['label']}: {_format_median_questions(row.get('value'))}"
        for row in rows
    ]
    return "\n" + "\n".join(lines)


def _donation_times_word(n: int) -> str:
    n_abs = abs(n) % 100
    n_mod = n_abs % 10
    if n_mod == 1 and n_abs != 11:
        return "раз"
    if n_mod in (2, 3, 4) and n_abs not in (12, 13, 14):
        return "раза"
    return "раз"


def _donations_count_word(n: int) -> str:
    n_abs = abs(n) % 100
    n_mod = n_abs % 10
    if n_mod == 1 and n_abs != 11:
        return "донат"
    if n_mod in (2, 3, 4) and n_abs not in (12, 13, 14):
        return "доната"
    return "донатов"


def _format_rub(amount: float) -> str:
    return f"{amount:,.0f}".replace(",", " ")


def _format_rub_delta(amount: float) -> str:
    sign = "+" if amount >= 0 else "-"
    return f"{sign}{_format_rub(abs(amount))} ₽"


def _format_donor_donation_distribution(
    distribution: list[tuple[int, int]],
) -> str:
    if not distribution:
        return ""
    lines = [
        f"      – {donations_count} {_donation_times_word(donations_count)}: {donors_count:,}"
        for donations_count, donors_count in distribution
    ]
    return "\n" + "\n".join(lines)


def _format_referral_depth_distribution(
    distribution: list[tuple[int, int]],
) -> str:
    if not distribution:
        return ""
    lines = [
        f"      – уровень {depth}: {count:,}"
        for depth, count in distribution
    ]
    return "\n" + "\n".join(lines)


def _format_top_referrers(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    lines: list[str] = []
    for row in rows:
        rid = int(row["referrer_id"])
        invites = int(row["invites"])
        fn = html.escape((row.get("first_name") or "").strip() or "—")
        un = (row.get("username") or "").strip().lstrip("@")
        label = f"{fn} @{html.escape(un)}" if un else fn
        lines.append(f"      – {label} (<code>{rid}</code>): {invites:,}")
    return "\n" + "\n".join(lines)


def _format_donations_revenue_by_month(
    rows: list[dict[str, Any]],
) -> str:
    """Помесячная выручка донатов; дельта — текущий месяц (1..N) минус тот же период в прошлом месяце."""
    if not rows:
        return ""

    current_partial = float(rows[0].get("partial_amount") or 0)
    lines: list[str] = []
    total = 0.0

    for i, row in enumerate(rows):
        month_start: date = row["month_start"]
        total_amount = float(row["total_amount"] or 0)
        cnt = int(row["donation_count"] or 0)
        total += total_amount

        month_key = f"{month_start.year}-{month_start.month:02d}"
        line = (
            f"    • {month_key}: {_format_rub(total_amount)} ₽ "
            f"({cnt:,} {_donations_count_word(cnt)})"
        )
        if i > 0:
            past_partial = float(row.get("partial_amount") or 0)
            line += f" {_format_rub_delta(current_partial - past_partial)}"
        lines.append(line)

    body = "\n".join(lines)
    return (
        f"\n\n"
        f"    💰 Всего выручка проекта\n"
        f"{body}\n"
        f"    ━━━━━━━━━━━━━━━━━━━━━\n"
        f"    📊 ИТОГО: {_format_rub(total)} ₽\n\n"
    )


class BibliaDailyReportCollector:
    def __init__(self, pool, *, snapshot_storage: Optional[MetricsSnapshotStorage] = None) -> None:
        self._pool = pool
        self._snapshots = snapshot_storage or MetricsSnapshotStorage(pool)

    async def _scalar(self, query: str, *args: Any) -> Any:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def _row(self, query: str, *args: Any) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, *args)
            return dict(row) if row else None

    async def _fetch(self, query: str, *args: Any) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
            return [dict(r) for r in rows]

    def _filtered_referrals_cte(self) -> str:
        return f"""
            filtered_referrals AS (
                SELECT referrer_id, referred_id
                FROM referrals
                WHERE TRUE
                  {_EXCLUDED_REFERRALS_FILTER}
            )
        """

    async def get_new_referrals(self, period_start: datetime, period_end: datetime) -> int:
        return int(
            await self._scalar(
                f"""
                SELECT COUNT(*)
                FROM referrals
                WHERE created_at >= $1
                  AND created_at < $2
                  {_EXCLUDED_REFERRALS_FILTER}
                """,
                period_start,
                period_end,
            )
            or 0
        )

    async def get_referrals_total(self) -> int:
        return int(
            await self._scalar(
                f"""
                SELECT COUNT(*)
                FROM referrals
                WHERE TRUE
                  {_EXCLUDED_REFERRALS_FILTER}
                """
            )
            or 0
        )

    async def get_referrals_unique_referrers(self) -> int:
        return int(
            await self._scalar(
                f"""
                SELECT COUNT(DISTINCT referrer_id)
                FROM referrals
                WHERE TRUE
                  {_EXCLUDED_REFERRALS_FILTER}
                """
            )
            or 0
        )

    async def get_referrals_unique_referred(self) -> int:
        return int(
            await self._scalar(
                f"""
                SELECT COUNT(DISTINCT referred_id)
                FROM referrals
                WHERE TRUE
                  {_EXCLUDED_REFERRALS_FILTER}
                """
            )
            or 0
        )

    async def get_referrals_paid_referred(self) -> int:
        return int(
            await self._scalar(
                f"""
                SELECT COUNT(DISTINCT r.referred_id)
                FROM referrals r
                JOIN payments p ON p.user_id::text = r.referred_id
                WHERE p.status = 'succeeded'
                  AND r.referrer_id::text IS DISTINCT FROM r.referred_id
                  AND r.referrer_id NOT IN ({', '.join(str(uid) for uid in _EXCLUDED_STATS_USER_IDS)})
                  AND r.referred_id NOT IN ({', '.join(f"'{uid}'" for uid in _EXCLUDED_STATS_USER_IDS)})
                """
            )
            or 0
        )

    async def get_referral_tree_stats(
        self,
    ) -> Tuple[int, list[tuple[int, int]]]:
        """Макс. глубина дерева и число приглашённых на каждом уровне от корней."""
        rows = await self._fetch(
            f"""
            WITH RECURSIVE
            {self._filtered_referrals_cte()},
            roots AS (
                SELECT DISTINCT f.referrer_id AS user_id
                FROM filtered_referrals f
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM filtered_referrals f2
                    WHERE f2.referred_id::bigint = f.referrer_id
                )
            ),
            tree AS (
                SELECT
                    f.referrer_id AS root_id,
                    f.referred_id::bigint AS user_id,
                    1 AS depth
                FROM filtered_referrals f
                INNER JOIN roots r ON r.user_id = f.referrer_id

                UNION ALL

                SELECT
                    t.root_id,
                    f.referred_id::bigint,
                    t.depth + 1
                FROM tree t
                INNER JOIN filtered_referrals f
                    ON f.referrer_id = t.user_id
                WHERE t.depth < {_REFERRAL_TREE_MAX_DEPTH}
            )
            SELECT depth, COUNT(*)::int AS referred_count
            FROM tree
            GROUP BY depth
            ORDER BY depth
            """
        )
        distribution = [(int(r["depth"]), int(r["referred_count"])) for r in rows]
        max_depth = distribution[-1][0] if distribution else 0
        return max_depth, distribution

    async def get_referral_top_referrers(self, limit: int = 5) -> list[dict[str, Any]]:
        return await self._fetch(
            f"""
            WITH
            {self._filtered_referrals_cte()}
            SELECT
                f.referrer_id,
                COUNT(*)::int AS invites,
                u.first_name,
                u.username
            FROM filtered_referrals f
            LEFT JOIN users u ON u.user_id = f.referrer_id
            GROUP BY f.referrer_id, u.first_name, u.username
            ORDER BY invites DESC, f.referrer_id
            LIMIT $1
            """,
            limit,
        )

    async def get_subscribers(self) -> int:
        return int(
            await self._scalar(
                "SELECT COUNT(*) FROM users WHERE is_active = true"
            )
            or 0
        )

    async def get_dau_without_new(self, day_start: datetime, day_end: datetime) -> Dict[str, Any]:
        row = await self._row(
            f"""
            WITH yesterday_users AS (
                SELECT DISTINCT m.user_id, DATE(u.created_at AT TIME ZONE 'Europe/Moscow') AS reg_date
                FROM messages m
                JOIN users u ON m.user_id = u.user_id
                WHERE {_USER_MSG_FILTER}
                  AND m.created_at >= $1
                  AND m.created_at < $2
            )
            SELECT
                COUNT(DISTINCT user_id) AS dau_total,
                COUNT(DISTINCT CASE
                    WHEN reg_date < ($1 AT TIME ZONE 'Europe/Moscow')::date
                    THEN user_id
                END) AS dau_without_new
            FROM yesterday_users
            """,
            day_start,
            day_end,
        ) or {}
        dau_total = int(row.get("dau_total") or 0)
        dau_without_new = int(row.get("dau_without_new") or 0)
        pct = round(dau_without_new * 100.0 / dau_total, 1) if dau_total else 0.0
        return {
            "dau_total": dau_total,
            "dau_without_new": dau_without_new,
            "pct_returning": pct,
        }

    async def get_mau_without_new(self, mau_start: datetime, mau_end: datetime) -> Dict[str, Any]:
        row = await self._row(
            f"""
            WITH last_30_days_users AS (
                SELECT
                    m.user_id,
                    DATE(u.created_at AT TIME ZONE 'Europe/Moscow') AS reg_date,
                    COUNT(DISTINCT DATE(m.created_at AT TIME ZONE 'Europe/Moscow')) AS active_days
                FROM messages m
                JOIN users u ON m.user_id = u.user_id
                WHERE {_USER_MSG_FILTER}
                  AND m.created_at >= $1
                  AND m.created_at < $2
                GROUP BY m.user_id, u.created_at
            )
            SELECT
                COUNT(DISTINCT user_id) AS mau_total,
                COUNT(DISTINCT CASE
                    WHEN active_days > 1
                      OR reg_date < ($1 AT TIME ZONE 'Europe/Moscow')::date
                    THEN user_id
                END) AS mau_without_new
            FROM last_30_days_users
            """,
            mau_start,
            mau_end,
        ) or {}
        mau_total = int(row.get("mau_total") or 0)
        mau_without_new = int(row.get("mau_without_new") or 0)
        pct = round(mau_without_new * 100.0 / mau_total, 1) if mau_total else 0.0
        return {
            "mau_total": mau_total,
            "mau_without_new": mau_without_new,
            "pct_returning": pct,
        }

    async def get_messages_yesterday(self, day_start: datetime, day_end: datetime) -> int:
        return int(
            await self._scalar(
                f"""
                SELECT COUNT(*)
                FROM messages m
                WHERE {_USER_MSG_FILTER}
                  AND {_NOT_VOICE_FILTER}
                  AND m.created_at >= $1
                  AND m.created_at < $2
                """,
                day_start,
                day_end,
            )
            or 0
        )

    async def get_avg_messages_per_user(self, day_start: datetime, day_end: datetime) -> float:
        val = await self._scalar(
            f"""
            WITH messages_stats AS (
                SELECT
                    COUNT(*) AS total_messages,
                    COUNT(DISTINCT user_id) AS unique_users
                FROM messages m
                WHERE {_USER_MSG_FILTER}
                  AND {_NOT_VOICE_FILTER}
                  AND m.user_id != $3
                  AND m.created_at >= $1
                  AND m.created_at < $2
            )
            SELECT CASE
                WHEN unique_users = 0 THEN 0
                ELSE ROUND(total_messages::decimal / unique_users, 2)
            END
            FROM messages_stats
            """,
            day_start,
            day_end,
            _EXCLUDED_DEPTH_USER_ID,
        )
        return float(val or 0)

    async def get_voice_messages_yesterday(self, day_start: datetime, day_end: datetime) -> int:
        return int(
            await self._scalar(
                f"""
                SELECT COUNT(*)
                FROM messages m
                WHERE {_USER_MSG_FILTER}
                  AND {_VOICE_FILTER}
                  AND m.created_at >= $1
                  AND m.created_at < $2
                """,
                day_start,
                day_end,
            )
            or 0
        )

    async def get_unique_voice_users_yesterday(self, day_start: datetime, day_end: datetime) -> int:
        return int(
            await self._scalar(
                f"""
                SELECT COUNT(DISTINCT user_id)
                FROM messages m
                WHERE {_USER_MSG_FILTER}
                  AND {_VOICE_FILTER}
                  AND m.created_at >= $1
                  AND m.created_at < $2
                """,
                day_start,
                day_end,
            )
            or 0
        )

    async def get_new_users(self, period_start: datetime, period_end: datetime) -> int:
        return int(
            await self._scalar(
                """
                SELECT COUNT(*)
                FROM users
                WHERE created_at >= $1 AND created_at < $2
                """,
                period_start,
                period_end,
            )
            or 0
        )

    async def get_donations_sum(self, period_start: datetime, period_end: datetime) -> float:
        val = await self._scalar(
            """
            SELECT COALESCE(SUM(amount_rub), 0)
            FROM payments
            WHERE status = 'succeeded'
              AND order_id IS NULL
              AND amount_rub IS NOT NULL
              AND created_at >= $1
              AND created_at < $2
            """,
            period_start,
            period_end,
        )
        return float(val or 0)

    async def get_donations_count(self, period_start: datetime, period_end: datetime) -> int:
        return int(
            await self._scalar(
                """
                SELECT COUNT(*)
                FROM payments
                WHERE status = 'succeeded'
                  AND order_id IS NULL
                  AND amount_rub IS NOT NULL
                  AND created_at >= $1
                  AND created_at < $2
                """,
                period_start,
                period_end,
            )
            or 0
        )

    async def get_donations_revenue_by_month(
        self, report_day: date
    ) -> list[dict[str, Any]]:
        """
        Выручка донатов по календарным месяцам (MSK), новые сверху.
        partial_amount — сумма с 1-го по report_day.day (включительно) внутри месяца.
        """
        compare_day = report_day.day
        rows = await self._fetch(
            """
            SELECT
                (date_trunc(
                    'month',
                    created_at AT TIME ZONE 'Europe/Moscow'
                ))::date AS month_start,
                COALESCE(SUM(amount_rub), 0)::float AS total_amount,
                COUNT(*)::int AS donation_count,
                COALESCE(SUM(amount_rub) FILTER (
                    WHERE EXTRACT(
                        DAY FROM created_at AT TIME ZONE 'Europe/Moscow'
                    ) <= $1
                ), 0)::float AS partial_amount
            FROM payments
            WHERE status = 'succeeded'
              AND order_id IS NULL
              AND amount_rub IS NOT NULL
            GROUP BY 1
            ORDER BY 1 DESC
            """,
            compare_day,
        )
        return rows

    async def get_unique_donors(self, period_start: datetime, period_end: datetime) -> int:
        return int(
            await self._scalar(
                f"""
                SELECT COUNT(DISTINCT user_id)
                FROM payments
                WHERE status = 'succeeded'
                  AND order_id IS NULL
                  AND amount_rub IS NOT NULL
                  AND created_at >= $1
                  AND created_at < $2
                  {_EXCLUDED_DONORS_FILTER}
                """,
                period_start,
                period_end,
            )
            or 0
        )

    async def get_median_questions_before_donation(
        self,
        *,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
    ) -> Optional[float]:
        """
        Медиана числа сообщений пользователя перед каждой успешной оплатой.
        Без period_* — все время; с period_* — только донаты в [start, end).
        Для первого доната — все вопросы до оплаты; для повторных — между прошлой и текущей.
        """
        val = await self._scalar(
            f"""
            WITH donations AS (
                SELECT
                    user_id,
                    COALESCE(completed_at, created_at) AS paid_at,
                    LAG(COALESCE(completed_at, created_at)) OVER (
                        PARTITION BY user_id
                        ORDER BY COALESCE(completed_at, created_at), id
                    ) AS prev_paid_at
                FROM payments
                WHERE status = 'succeeded'
                  AND order_id IS NULL
                  {_EXCLUDED_DONORS_FILTER}
            ),
            filtered_donations AS (
                SELECT user_id, paid_at, prev_paid_at
                FROM donations
                WHERE ($1::timestamptz IS NULL OR paid_at >= $1)
                  AND ($2::timestamptz IS NULL OR paid_at < $2)
            ),
            question_counts AS (
                SELECT (
                    SELECT COUNT(*)::int
                    FROM messages m
                    WHERE m.user_id = fd.user_id
                      AND {_USER_MSG_FILTER}
                      AND m.created_at < fd.paid_at
                      AND (
                        fd.prev_paid_at IS NULL
                        OR m.created_at > fd.prev_paid_at
                      )
                ) AS questions_before
                FROM filtered_donations fd
            )
            SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY questions_before)
            FROM question_counts
            """,
            period_start,
            period_end,
        )
        return float(val) if val is not None else None

    async def get_avg_donations_per_donor(self) -> float:
        """Среднее число успешных донатов на одного донатера (все время)."""
        val = await self._scalar(
            f"""
            SELECT ROUND(AVG(cnt)::numeric, 2)
            FROM (
                SELECT COUNT(*)::int AS cnt
                FROM payments
                WHERE status = 'succeeded'
                  AND order_id IS NULL
                  {_EXCLUDED_DONORS_FILTER}
                GROUP BY user_id
            ) donors
            """
        )
        return float(val or 0)

    async def get_donor_donation_count_distribution(self) -> list[tuple[int, int]]:
        """Число донатеров с 1, 2, 3, … успешными донатами (все время)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT cnt AS donations_count, COUNT(*)::int AS donors_count
                FROM (
                    SELECT COUNT(*)::int AS cnt
                    FROM payments
                    WHERE status = 'succeeded'
                      AND order_id IS NULL
                      {_EXCLUDED_DONORS_FILTER}
                    GROUP BY user_id
                ) per_donor
                GROUP BY cnt
                ORDER BY cnt
                """
            )
        return [(int(r["donations_count"]), int(r["donors_count"])) for r in rows]

    async def get_total_donation_shows(self) -> int:
        return int(
            await self._scalar("SELECT COALESCE(SUM(donation_button), 0) FROM users") or 0
        )

    async def get_total_donation_clicks(self) -> int:
        return int(
            await self._scalar("SELECT COALESCE(SUM(donation_button_click), 0) FROM users") or 0
        )

    async def get_total_donation_proposals(self) -> int:
        return int(
            await self._scalar(
                "SELECT COALESCE(SUM(donation_proposal_count), 0) FROM users"
            )
            or 0
        )

    async def get_users_never_received_blessings(self) -> int:
        """Активные пользователи без успешной рассылки из кампаний с «(авто)» в названии."""
        return int(
            await self._scalar(
                """
                SELECT COUNT(*)
                FROM users u
                WHERE u.is_active = true
                  AND NOT EXISTS (
                    SELECT 1
                    FROM mailing_audience ma
                    JOIN mailing_campaigns mc ON mc.id = ma.campaign_id
                    WHERE ma.user_id = u.user_id
                      AND mc.name ILIKE '%(авто)%'
                      AND ma.status = 'sent'
                  )
                """
            )
            or 0
        )

    async def _daily_donation_engagement(
        self, previous_snapshot: Optional[Dict[str, Any]]
    ) -> Dict[str, int]:
        current_shows = await self.get_total_donation_shows()
        current_clicks = await self.get_total_donation_clicks()
        current_proposals = await self.get_total_donation_proposals()

        if previous_snapshot:
            shows = current_shows - int(previous_snapshot.get("donation_buttons_shown") or 0)
            clicks = current_clicks - int(previous_snapshot.get("donation_button_clicks") or 0)
            proposals = current_proposals - int(previous_snapshot.get("donation_proposals") or 0)
        else:
            shows, clicks, proposals = current_shows, current_clicks, current_proposals

        return {
            "shows_yesterday": max(0, shows),
            "clicks_yesterday": max(0, clicks),
            "proposals_yesterday": max(0, proposals),
            "donation_buttons_shown": current_shows,
            "donation_button_clicks": current_clicks,
            "donation_proposals": current_proposals,
        }

    async def get_all_metrics(self, *, save_snapshot: bool = True) -> Dict[str, Any]:
        now_msk = datetime.now(_MSK)
        report_day = now_msk.date() - timedelta(days=1)
        day_start, day_end = _msk_day_bounds(report_day)
        mau_start = day_end - timedelta(days=30)
        month_start = _msk_month_start(report_day)
        month_end = day_end
        users_30d_start = day_end - timedelta(days=30)

        prev_month_day = report_day.replace(day=1) - timedelta(days=1)
        prev2_month_day = prev_month_day.replace(day=1) - timedelta(days=1)

        median_by_month = [
            {
                "label": _month_key(report_day),
                "value": await self.get_median_questions_before_donation(
                    period_start=month_start,
                    period_end=month_end,
                ),
            },
            {
                "label": _month_key(prev_month_day),
                "value": await self.get_median_questions_before_donation(
                    period_start=_msk_month_start(prev_month_day),
                    period_end=_msk_month_end_exclusive(prev_month_day),
                ),
            },
            {
                "label": _month_key(prev2_month_day),
                "value": await self.get_median_questions_before_donation(
                    period_start=_msk_month_start(prev2_month_day),
                    period_end=_msk_month_end_exclusive(prev2_month_day),
                ),
            },
        ]

        dau_stats = await self.get_dau_without_new(day_start, day_end)
        mau_stats = await self.get_mau_without_new(mau_start, day_end)

        prev_snapshot = await self._snapshots.get_snapshot(
            report_day - timedelta(days=1)
        )
        donation_daily = await self._daily_donation_engagement(prev_snapshot)

        donations_count = await self.get_donations_count(day_start, day_end)
        unique_donors = await self.get_unique_donors(day_start, day_end)

        referrals_referred = await self.get_referrals_unique_referred()
        referrals_paid = await self.get_referrals_paid_referred()
        referral_max_depth, referral_depth_dist = await self.get_referral_tree_stats()

        metrics: Dict[str, Any] = {
            "period": f"Данные за {report_day.strftime('%d.%m.%Y')}",
            "report_day": report_day.isoformat(),
            "month_period": (
                f"{report_day.strftime('%B')} "
                f"(1-{report_day.day}.{report_day.strftime('%m.%Y')})"
            ),
            "subscribers": await self.get_subscribers(),
            "dau": dau_stats["dau_total"],
            "dau_without_new": dau_stats["dau_without_new"],
            "dau_returning_pct": dau_stats["pct_returning"],
            "mau": mau_stats["mau_total"],
            "mau_without_new": mau_stats["mau_without_new"],
            "mau_returning_pct": mau_stats["pct_returning"],
            "messages": await self.get_messages_yesterday(day_start, day_end),
            "avg_messages_per_user": await self.get_avg_messages_per_user(day_start, day_end),
            "voice_messages": await self.get_voice_messages_yesterday(day_start, day_end),
            "unique_voice_users": await self.get_unique_voice_users_yesterday(day_start, day_end),
            "new_users_yesterday": await self.get_new_users(day_start, day_end),
            "new_users_30d": await self.get_new_users(users_30d_start, day_end),
            "new_referrals_yesterday": await self.get_new_referrals(day_start, day_end),
            "new_referrals_30d": await self.get_new_referrals(users_30d_start, day_end),
            "referrals_total": await self.get_referrals_total(),
            "referrals_unique_referrers": await self.get_referrals_unique_referrers(),
            "referrals_unique_referred": referrals_referred,
            "referrals_paid_referred": referrals_paid,
            "referrals_paid_pct": (
                round(referrals_paid * 100.0 / referrals_referred, 1)
                if referrals_referred
                else 0.0
            ),
            "referral_tree_max_depth": referral_max_depth,
            "referral_tree_depth_distribution": referral_depth_dist,
            "referral_top_referrers": await self.get_referral_top_referrers(5),
            "donations_yesterday": await self.get_donations_sum(day_start, day_end),
            "donations_count": donations_count,
            "unique_donors": unique_donors,
            "donations_month_to_date": await self.get_donations_sum(month_start, month_end),
            "donations_revenue_by_month": await self.get_donations_revenue_by_month(
                report_day
            ),
            "median_questions_before_donation": await self.get_median_questions_before_donation(),
            "median_questions_by_month": median_by_month,
            "avg_donations_per_donor": await self.get_avg_donations_per_donor(),
            "donor_donation_count_distribution": await self.get_donor_donation_count_distribution(),
            "donation_shows_yesterday": donation_daily["shows_yesterday"],
            "donation_clicks_yesterday": donation_daily["clicks_yesterday"],
            "donation_proposals_yesterday": donation_daily["proposals_yesterday"],
            "donation_buttons_shown": donation_daily["donation_buttons_shown"],
            "donation_button_clicks": donation_daily["donation_button_clicks"],
            "donation_proposals": donation_daily["donation_proposals"],
            "users_never_mailed": await self.get_users_never_received_blessings(),
        }

        clicks = metrics["donation_clicks_yesterday"]
        metrics["donation_conversion"] = (
            round((donations_count / clicks) * 100, 1) if clicks > 0 else 0.0
        )

        if save_snapshot:
            await self._snapshots.save_snapshot(metrics)
        return metrics

    @staticmethod
    def format_report(metrics: Dict[str, Any]) -> str:
        voice_count = int(metrics.get("voice_messages", 0))
        total_messages = int(metrics.get("messages", 0))
        voice_percent = (
            round((voice_count / total_messages * 100), 1) if total_messages > 0 else 0.0
        )

        dau = int(metrics.get("dau", 0))
        unique_voice_users = int(metrics.get("unique_voice_users", 0))
        voice_users_percent = (
            round((unique_voice_users / dau * 100), 1) if dau > 0 else 0.0
        )

        clicks = int(metrics.get("donation_clicks_yesterday", 0))
        proposals = int(metrics.get("donation_proposals_yesterday", 0))
        shows = int(metrics.get("donation_shows_yesterday", 0))
        total_opportunities = proposals + shows
        click_rate = (
            round((clicks / total_opportunities * 100), 1) if total_opportunities > 0 else 0.0
        )

        total_calls = proposals + shows
        calls_percent = (
            round((total_calls / total_messages * 100), 1) if total_messages > 0 else 0.0
        )

        unique_donors = int(metrics.get("unique_donors", 0))
        conversion = (
            round((unique_donors / clicks) * 100, 1) if clicks > 0 else 0.0
        )

        median_questions = metrics.get("median_questions_before_donation")
        median_questions_s = _format_median_questions(median_questions)
        median_by_month_s = _format_median_questions_by_month(
            metrics.get("median_questions_by_month") or []
        )

        avg_donations = metrics.get("avg_donations_per_donor", 0)
        if avg_donations == int(avg_donations):
            avg_donations_s = f"{int(avg_donations)}"
        else:
            avg_donations_s = f"{avg_donations:.2f}"

        donor_distribution_s = _format_donor_donation_distribution(
            metrics.get("donor_donation_count_distribution") or []
        )

        donations_by_month_s = _format_donations_revenue_by_month(
            metrics.get("donations_revenue_by_month") or [],
        )

        referral_depth_s = _format_referral_depth_distribution(
            metrics.get("referral_tree_depth_distribution") or []
        )
        referral_top_s = _format_top_referrers(
            metrics.get("referral_top_referrers") or []
        )
        referrals_referred = int(metrics.get("referrals_unique_referred", 0))
        referrals_paid = int(metrics.get("referrals_paid_referred", 0))

        return f"""<b>🤖 БИБЛИЯ</b>
    <i>{metrics['period']}</i>
    • Подписчиков всего: {metrics['subscribers']:,}

    <b>👥 АКТИВНОСТЬ (за вчера)</b>
    • DAU: {metrics['dau']:,}
    • Из них "старых": {metrics.get('dau_without_new', 0):,} ({metrics.get('dau_returning_pct', 0)}%)
    • MAU (30 дней): {metrics['mau']:,}
    • Из них "старых": {metrics.get('mau_without_new', 0):,} ({metrics.get('mau_returning_pct', 0)}%)
    • Сообщений: {metrics['messages']:,}
    • Глубина общения: {metrics['avg_messages_per_user']}
    • Голосовых сообщений: {metrics.get('voice_messages', 0)} ({voice_percent}%)
    • Пользователей с аудио: {metrics.get('unique_voice_users', 0)} ({voice_users_percent}%)

    <b>🆕 НОВЫЕ ПОЛЬЗОВАТЕЛИ</b>
    • За вчера: {metrics['new_users_yesterday']:,}
    • За 30 дней: {metrics['new_users_30d']:,}

    <b>🔗 РЕФЕРАЛЬНАЯ ПРОГРАММА</b>
    <i>без учёта {', '.join(str(uid) for uid in _EXCLUDED_STATS_USER_IDS)}</i>
    • Переходов по ссылкам (всего): {metrics.get('referrals_total', 0):,}
    • Уникальных пригласивших: {metrics.get('referrals_unique_referrers', 0):,}
    • Уникальных приглашённых: {referrals_referred:,}
    • С оплатой среди приглашённых: {referrals_paid:,} ({metrics.get('referrals_paid_pct', 0)}%)
    • За вчера: {metrics['new_referrals_yesterday']:,}
    • За 30 дней: {metrics['new_referrals_30d']:,}
    • Глубина дерева (макс.): {metrics.get('referral_tree_max_depth', 0)}{referral_depth_s}
    • Топ пригласивших:{referral_top_s or chr(10) + '      – нет данных'}

    <b>💰 ДОНАТЫ</b>
    • Сумма за вчера: {metrics['donations_yesterday']:,.0f} ₽
    • Количество донатов: {metrics.get('donations_count', 0)}
    • Уникальных донатеров: {metrics.get('unique_donors', 0)}
    • {metrics['month_period']}: {metrics['donations_month_to_date']:,.0f} ₽{donations_by_month_s}
    • Медиана вопросов до доната (всего): {median_questions_s}{median_by_month_s}

    • Среднее донатов на донатера (всего): {avg_donations_s}{donor_distribution_s}

    <b>🖱️ ВОВЛЕЧЕНИЕ В ДОНАТЫ (за вчера)</b>
    • Предложений доната: {metrics.get('donation_proposals_yesterday', 0)}
    • Показов кнопок: {metrics.get('donation_shows_yesterday', 0)}
    • Всего призывов к донатам: {total_calls} ({calls_percent}% от сообщений)
    • Нажатий на кнопку: {clicks} ({click_rate}% от показов+предложений)
    • Конверсия (нажатие кнопки -> оплата): {conversion}%

    <b>📨 НИ РАЗУ НЕ ПОЛУЧИВШИЕ БЛАГОСЛОВЕНИЯ</b>
    • Без рассылок: {metrics.get('users_never_mailed', 0)}"""
