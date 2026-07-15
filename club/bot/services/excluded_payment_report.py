"""Отчёт по отвалившимся: профиль оплат по тарифам."""

from __future__ import annotations

import html as html_mod
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from bot.services.report_exclude import sql_exclude_users

if TYPE_CHECKING:
    import asyncpg

EXCLUDED_REPORT_PERIOD_DAYS: tuple[int, ...] = (7, 14, 21, 30, 60, 90)

FAMILY_LABELS: Dict[str, str] = {
    "td": "ТД",
    "1m": "1 мес",
    "3m": "3 мес",
    "6m": "6 мес",
    "12m": "12 мес",
    "other": "Другой тариф",
}

FAMILY_SORT_ORDER: tuple[str, ...] = ("td", "1m", "3m", "6m", "12m", "other")

_PROFILE_SQL = """
WITH churned AS (
    SELECT
        l.user_id,
        COALESCE(
            (
                SELECT MAX(lh.created_at)
                FROM license_history lh
                WHERE lh.user_id = l.user_id
                  AND lh.source IN ('subscription_expired', 'expired_detected_on_read')
            ),
            l.updated_at,
            l.expires_at
        ) AS churned_at
    FROM license l
    WHERE l.status = 'expired'
      AND NOT EXISTS (
          SELECT 1
          FROM license l2
          WHERE l2.user_id = l.user_id
            AND l2.status = 'active'
            AND l2.expires_at > NOW()
      )
      {exclude_license}
    GROUP BY l.user_id, l.updated_at, l.expires_at
),
cohort AS (
    SELECT user_id, churned_at
    FROM churned
    WHERE ($1::int IS NULL OR churned_at >= NOW() - make_interval(days => $1::int))
),
family_counts AS (
    SELECT
        o.user_id,
        CASE
            WHEN COALESCE(t.type, '') LIKE 'promo_test1week%' THEN 'td'
            WHEN COALESCE(t.duration_days, 0) <= 45 THEN '1m'
            WHEN t.duration_days <= 120 THEN '3m'
            WHEN t.duration_days <= 270 THEN '6m'
            WHEN t.duration_days > 270 THEN '12m'
            ELSE 'other'
        END AS tariff_family,
        COUNT(*)::int AS pay_n
    FROM orders o
    JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
    JOIN tariffs t ON t.id = o.tariff_id
    JOIN cohort c ON c.user_id = o.user_id
    WHERE o.status = 'paid'
    GROUP BY o.user_id, tariff_family
),
profiles AS (
    SELECT
        user_id,
        COUNT(*)::int AS families,
        jsonb_object_agg(tariff_family, pay_n) AS profile
    FROM family_counts
    GROUP BY user_id
)
SELECT
    (SELECT COUNT(*)::bigint FROM cohort) AS total_churned,
    (
        SELECT COUNT(*)::bigint
        FROM cohort c
        JOIN club_member_exclusions cme ON cme.user_id = c.user_id
    ) AS kicked_from_group,
    (
        SELECT COUNT(*)::bigint FROM cohort c
        WHERE NOT EXISTS (
            SELECT 1 FROM orders o
            JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
            WHERE o.user_id = c.user_id AND o.status = 'paid'
        )
    ) AS no_payments,
    COALESCE(
        (
            SELECT jsonb_agg(
                jsonb_build_object('user_id', user_id, 'families', families, 'profile', profile)
            )
            FROM profiles
        ),
        '[]'::jsonb
    ) AS user_profiles
"""

_NO_PAY_DETAIL_SQL = """
WITH churned AS (
    SELECT
        l.user_id,
        COALESCE(
            (
                SELECT MAX(lh.created_at)
                FROM license_history lh
                WHERE lh.user_id = l.user_id
                  AND lh.source IN ('subscription_expired', 'expired_detected_on_read')
            ),
            l.updated_at,
            l.expires_at
        ) AS churned_at
    FROM license l
    WHERE l.status = 'expired'
      AND NOT EXISTS (
          SELECT 1
          FROM license l2
          WHERE l2.user_id = l.user_id
            AND l2.status = 'active'
            AND l2.expires_at > NOW()
      )
      {exclude_license}
    GROUP BY l.user_id, l.updated_at, l.expires_at
),
cohort AS (
    SELECT user_id, churned_at
    FROM churned
    WHERE ($1::int IS NULL OR churned_at >= NOW() - make_interval(days => $1::int))
),
no_pay AS (
    SELECT c.user_id
    FROM cohort c
    WHERE NOT EXISTS (
        SELECT 1
        FROM orders o
        JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
        WHERE o.user_id = c.user_id
          AND o.status = 'paid'
    )
),
admin_grant_info AS (
    SELECT DISTINCT ON (lh.user_id)
        lh.user_id,
        (lh.meta ->> 'days_added')::int AS admin_days,
        (lh.meta ->> 'admin_telegram_id')::bigint AS admin_id
    FROM license_history lh
    JOIN no_pay n ON n.user_id = lh.user_id
    WHERE lh.source = 'admin_grant'
    ORDER BY lh.user_id, lh.created_at ASC
),
gift_info AS (
    SELECT DISTINCT ON (g.activated_by)
        g.activated_by AS user_id,
        g.user_id AS donor_id
    FROM gifts g
    JOIN no_pay n ON n.user_id = g.activated_by
    WHERE g.activated_by IS NOT NULL
    ORDER BY g.activated_by, g.activated_at ASC NULLS LAST
),
member_gift_info AS (
    SELECT DISTINCT ON (o.gift_recipient_user_id)
        o.gift_recipient_user_id AS user_id,
        o.user_id AS donor_id
    FROM orders o
    JOIN no_pay n ON n.user_id = o.gift_recipient_user_id
    JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
    WHERE o.status = 'paid'
      AND o.gift_recipient_user_id IS NOT NULL
    ORDER BY o.gift_recipient_user_id, o.paid_at ASC
),
first_license_source AS (
    SELECT DISTINCT ON (lh.user_id)
        lh.user_id,
        lh.source
    FROM license_history lh
    JOIN no_pay n ON n.user_id = lh.user_id
    ORDER BY lh.user_id, lh.created_at ASC
)
SELECT
    n.user_id,
    u.first_name,
    u.username,
    ag.admin_days,
    ag.admin_id,
    au.first_name AS admin_first_name,
    au.username AS admin_username,
    gi.donor_id AS gift_donor_id,
    du.first_name AS gift_donor_first_name,
    du.username AS gift_donor_username,
    mg.donor_id AS member_gift_donor_id,
    mdu.first_name AS member_gift_donor_first_name,
    mdu.username AS member_gift_donor_username,
    fls.source AS first_license_source
FROM no_pay n
JOIN users u ON u.user_id = n.user_id
LEFT JOIN admin_grant_info ag ON ag.user_id = n.user_id
LEFT JOIN users au ON au.user_id = ag.admin_id
LEFT JOIN gift_info gi ON gi.user_id = n.user_id
LEFT JOIN users du ON du.user_id = gi.donor_id
LEFT JOIN member_gift_info mg ON mg.user_id = n.user_id
LEFT JOIN users mdu ON mdu.user_id = mg.donor_id
LEFT JOIN first_license_source fls ON fls.user_id = n.user_id
ORDER BY n.user_id
"""

_LICENSE_SOURCE_LABELS: Dict[str, str] = {
    "gift_activation": "Активация подарочной ссылки",
    "admin_grant": "Админ /gift",
    "admin_subscription": "Админская подписка",
    "member_gift_extension": "Подарок от участника клуба",
    "referral_bonus": "Реферальный бонус",
    "angel_pool": "Бонус ангельского пула",
    "bonus_extension_offer": "Бонусное продление",
    "nastya_start_gift": "Стартовый подарок (Nastya)",
}


@dataclass
class NoPayUserDetail:
    user_id: int
    first_name: Optional[str]
    username: Optional[str]
    access_source: str


@dataclass
class PaymentProfileBucket:
    profile: Dict[str, int]
    users: int

    @property
    def label(self) -> str:
        return format_profile_label(self.profile)

    @property
    def sort_key(self) -> Tuple[Any, ...]:
        return profile_sort_key(self.profile)


@dataclass
class ExcludedPaymentReport:
    period_days: Optional[int]
    total_churned: int
    kicked_from_group: int
    no_payments: int
    buckets: List[PaymentProfileBucket] = field(default_factory=list)
    no_pay_details: List[NoPayUserDetail] = field(default_factory=list)


def _period_title(days: Optional[int]) -> str:
    if days is None:
        return "за всё время проекта"
    return f"за последние {days} дн."


def _parse_profile(raw: Any) -> Dict[str, int]:
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, int] = {}
    for k, v in raw.items():
        try:
            out[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out


def profile_sort_key(profile: Dict[str, int]) -> Tuple[Any, ...]:
    def fam_order(fam: str) -> int:
        try:
            return FAMILY_SORT_ORDER.index(fam)
        except ValueError:
            return 99

    return tuple(
        sorted(
            profile.items(),
            key=lambda item: (fam_order(item[0]), item[1]),
        )
    )


def _user_short_label(
    *,
    user_id: int,
    first_name: Optional[str],
    username: Optional[str],
) -> str:
    parts: List[str] = [f"<code>{user_id}</code>"]
    name = (first_name or "").strip()
    if name:
        parts.append(html_mod.escape(name))
    un = (username or "").strip()
    if un:
        parts.append(f"@{html_mod.escape(un)}")
    return " ".join(parts)


def _donor_label(
    donor_id: Optional[int],
    first_name: Optional[str],
    username: Optional[str],
) -> str:
    if not donor_id:
        return "неизвестный даритель"
    name = (first_name or "").strip()
    if name and username:
        return f"{html_mod.escape(name)} (@{html_mod.escape(username)})"
    if username:
        return f"@{html_mod.escape(username)}"
    if name:
        return html_mod.escape(name)
    return f"<code>{donor_id}</code>"


def _resolve_no_pay_access_source(row: Dict[str, Any]) -> str:
    if row.get("gift_donor_id"):
        donor = _donor_label(
            row.get("gift_donor_id"),
            row.get("gift_donor_first_name"),
            row.get("gift_donor_username"),
        )
        return f"Подарок от {donor}"
    if row.get("member_gift_donor_id"):
        donor = _donor_label(
            row.get("member_gift_donor_id"),
            row.get("member_gift_donor_first_name"),
            row.get("member_gift_donor_username"),
        )
        return f"Подарок участника от {donor}"
    if row.get("admin_id"):
        admin = _donor_label(
            row.get("admin_id"),
            row.get("admin_first_name"),
            row.get("admin_username"),
        )
        days = row.get("admin_days")
        if days:
            return f"/gift админом {admin} ({int(days)} дн.)"
        return f"/gift админом {admin}"
    source = str(row.get("first_license_source") or "").strip()
    if source:
        return _LICENSE_SOURCE_LABELS.get(source, source)
    return "Источник не определён"


def format_profile_label(profile: Dict[str, int]) -> str:
    if not profile:
        return "Без оплат"
    parts: List[str] = []
    for fam, pay_n in profile_sort_key(profile):
        label = FAMILY_LABELS.get(fam, fam)
        parts.append(f"{label} ×{pay_n}")
    return " + ".join(parts)


def _aggregate_profiles(rows: List[Dict[str, Any]]) -> Dict[Tuple[Tuple[str, int], ...], int]:
    """Группирует людей по полному профилю оплат (включая смешанные)."""
    bucket_counts: Dict[Tuple[Tuple[str, int], ...], int] = {}
    for row in rows:
        profile = _parse_profile(row.get("profile"))
        key = profile_sort_key(profile)
        bucket_counts[key] = bucket_counts.get(key, 0) + 1
    return bucket_counts


async def collect_excluded_payment_report(
    pool: "asyncpg.Pool",
    period_days: Optional[int],
) -> ExcludedPaymentReport:
    if period_days is not None:
        period_days = max(1, min(int(period_days), 3650))
    ex_sql, ex_args = sql_exclude_users("l.user_id", start_param=2)
    query = _PROFILE_SQL.format(exclude_license=ex_sql)
    no_pay_query = _NO_PAY_DETAIL_SQL.format(exclude_license=ex_sql)
    bind_days = period_days
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, bind_days, *ex_args)
        no_pay_rows = await conn.fetch(no_pay_query, bind_days, *ex_args)

    data = dict(row or {})
    profiles_raw = data.get("user_profiles") or []
    if isinstance(profiles_raw, str):
        profiles_raw = json.loads(profiles_raw)
    profile_rows = [dict(x) for x in (profiles_raw or [])]

    bucket_counts = _aggregate_profiles(profile_rows)
    buckets: List[PaymentProfileBucket] = [
        PaymentProfileBucket(profile=dict(key), users=cnt)
        for key, cnt in bucket_counts.items()
    ]
    buckets.sort(key=lambda b: (len(b.profile), b.sort_key))

    no_payments = int(data.get("no_payments") or 0)
    if no_payments and not any(not b.profile for b in buckets):
        buckets.insert(
            0,
            PaymentProfileBucket(profile={}, users=no_payments),
        )

    no_pay_details = [
        NoPayUserDetail(
            user_id=int(r["user_id"]),
            first_name=r.get("first_name"),
            username=r.get("username"),
            access_source=_resolve_no_pay_access_source(dict(r)),
        )
        for r in (no_pay_rows or [])
    ]

    return ExcludedPaymentReport(
        period_days=period_days,
        total_churned=int(data.get("total_churned") or 0),
        kicked_from_group=int(data.get("kicked_from_group") or 0),
        no_payments=no_payments,
        buckets=buckets,
        no_pay_details=no_pay_details,
    )


def format_excluded_payment_html(report: ExcludedPaymentReport) -> str:
    lines = [
        "<b>📉 Отвалившиеся (просрочка)</b>",
        f"<i>Период окончания подписки: {_period_title(report.period_days)}</i>",
        "",
        f"<b>Всего отвалилось:</b> {report.total_churned} чел.",
        (
            f"<i>Из них с записью исключения из группы: "
            f"{report.kicked_from_group} чел.</i>"
        ),
        "",
        "<b>Профиль оплат по людям:</b>",
    ]

    if report.buckets:
        for b in report.buckets:
            lines.append(f"• {html_mod.escape(b.label)} — {b.users} чел.")
    else:
        lines.append("• <i>нет данных об оплатах</i>")

    if report.no_pay_details:
        lines.append("")
        lines.append("<b>Без оплат — расшифровка:</b>")
        for detail in report.no_pay_details:
            label = _user_short_label(
                user_id=detail.user_id,
                first_name=detail.first_name,
                username=detail.username,
            )
            lines.append(
                f"• {label} — {html_mod.escape(detail.access_source)}"
            )

    lines.append("")
    lines.append(
        "<i>Когорта: <code>license.status=expired</code>, нет активной лицензии. "
        "ТД — <code>promo_test1week*</code>; 1/3/6/12 мес — по "
        "<code>duration_days</code>. Запись в "
        "<code>club_member_exclusions</code> — только факт кика из группы "
        "(обычно меньше, чем все просроченные).</i>"
    )
    return "\n".join(lines)
