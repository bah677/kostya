"""Быстрые «сейчас»-отчёты для /adm."""

from __future__ import annotations

import html as html_mod
import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from bot.services.biblia_daily_report import (
    BibliaDailyReportCollector,
    _EXCLUDED_STATS_USER_IDS,
)

logger = logging.getLogger(__name__)

_MSK = ZoneInfo("Europe/Moscow")

_STATUS_LABELS = {
    "planned": "⏳ planned",
    "running": "▶️ running",
    "completed": "✅ completed",
    "failed": "❌ failed",
    "cancelled": "🚫 cancelled",
}


def _msk_now() -> datetime:
    return datetime.now(_MSK)


def _msk_day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=_MSK)
    return start, start + timedelta(days=1)


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


def _status_label(status: str) -> str:
    key = (status or "").strip().lower()
    return _STATUS_LABELS.get(key, html_mod.escape(status or "—"))


async def fetch_last_campaigns(
    pool, *, limit: int = 3
) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                c.id,
                c.name,
                c.status,
                c.scheduled_at,
                c.created_at,
                c.updated_at,
                COALESCE(c.sent_count, 0) AS sent_count,
                COALESCE(c.failed_count, 0) AS failed_count,
                COALESCE(c.blocked_count, 0) AS blocked_count,
                COALESCE(a.audience_total, 0) AS audience_total,
                COALESCE(a.pending_count, 0) AS pending_count,
                COALESCE(a.processing_count, 0) AS processing_count,
                COALESCE(a.sent_audience, 0) AS sent_audience,
                COALESCE(a.failed_audience, 0) AS failed_audience,
                COALESCE(a.blocked_audience, 0) AS blocked_audience
            FROM mailing_campaigns c
            LEFT JOIN LATERAL (
                SELECT
                    COUNT(*) AS audience_total,
                    COUNT(*) FILTER (WHERE status = 'pending') AS pending_count,
                    COUNT(*) FILTER (WHERE status = 'processing') AS processing_count,
                    COUNT(*) FILTER (WHERE status = 'sent') AS sent_audience,
                    COUNT(*) FILTER (WHERE status = 'failed') AS failed_audience,
                    COUNT(*) FILTER (WHERE status = 'blocked') AS blocked_audience
                FROM mailing_audience ma
                WHERE ma.campaign_id = c.id
            ) a ON TRUE
            ORDER BY c.id DESC
            LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]


def format_last_campaigns_report(campaigns: List[Dict[str, Any]]) -> str:
    now_s = _msk_now().strftime("%d.%m.%Y %H:%M MSK")
    parts = [
        "<b>📨 Последние 3 кампании</b>",
        f"<i>на сейчас · {html_mod.escape(now_s)}</i>",
        "",
    ]
    if not campaigns:
        parts.append("Кампаний пока нет.")
        return "\n".join(parts)

    for i, c in enumerate(campaigns, start=1):
        name = html_mod.escape(str(c.get("name") or "без имени"))
        cid = int(c.get("id") or 0)
        total = int(c.get("audience_total") or 0)
        sent = int(c.get("sent_count") or 0)
        failed = int(c.get("failed_count") or 0)
        blocked = int(c.get("blocked_count") or 0)
        pending = int(c.get("pending_count") or 0)
        processing = int(c.get("processing_count") or 0)
        sent_aud = int(c.get("sent_audience") or 0)
        open_left = pending + processing
        done_pct = 0
        if total > 0:
            done_pct = round(100.0 * sent_aud / total, 1)

        parts.extend(
            [
                f"<b>{i}. #{cid}</b> — {name}",
                f"• статус: {_status_label(str(c.get('status') or ''))}",
                f"• план: {_fmt_dt(c.get('scheduled_at'))} · обновл.: {_fmt_dt(c.get('updated_at'))}",
                f"• аудитория: <b>{total}</b>",
                (
                    f"• прогресс: sent <b>{sent}</b> / failed {failed} / blocked {blocked}"
                    f" · осталось {open_left} ({done_pct}% sent)"
                ),
                (
                    f"• аудитория по статусам: pending {pending}, processing {processing},"
                    f" sent {sent_aud}, failed {int(c.get('failed_audience') or 0)},"
                    f" blocked {int(c.get('blocked_audience') or 0)}"
                ),
                "",
            ]
        )
    return "\n".join(parts).rstrip()


async def format_prayer_usage_report(pool) -> str:
    collector = BibliaDailyReportCollector(pool)
    now = _msk_now()
    today = now.date()
    today_start, today_end = _msk_day_bounds(today)
    yday_start, yday_end = _msk_day_bounds(today - timedelta(days=1))
    d7_start = today_start - timedelta(days=6)
    d30_start = today_start - timedelta(days=29)
    epoch = datetime(2020, 1, 1, tzinfo=_MSK)

    today_s = await collector.get_prayer_generation_stats(today_start, today_end)
    yday_s = await collector.get_prayer_generation_stats(yday_start, yday_end)
    d7_s = await collector.get_prayer_generation_stats(d7_start, today_end)
    d30_s = await collector.get_prayer_generation_stats(d30_start, today_end)
    all_s = await collector.get_prayer_generation_stats(epoch, today_end)

    first_at: Optional[datetime] = None
    last_at: Optional[datetime] = None
    excl = ", ".join(str(uid) for uid in _EXCLUDED_STATS_USER_IDS)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT MIN(created_at) AS first_at, MAX(created_at) AS last_at
            FROM token_usage
            WHERE request_kind LIKE 'personal_prayer_compose%'
              AND user_id NOT IN ({excl})
            """
        )
        if row:
            first_at = row["first_at"]
            last_at = row["last_at"]

    now_s = now.strftime("%d.%m.%Y %H:%M MSK")
    excl_s = ", ".join(str(uid) for uid in _EXCLUDED_STATS_USER_IDS)

    def _line(label: str, stats: Dict[str, int]) -> str:
        return (
            f"• {label}: <b>{int(stats.get('generations_count') or 0)}</b> генераций / "
            f"<b>{int(stats.get('unique_users') or 0)}</b> уников"
        )

    return "\n".join(
        [
            "<b>🙏 Голосовая молитва — использование</b>",
            f"<i>на сейчас · {html_mod.escape(now_s)}</i>",
            f"<i>без учёта {html_mod.escape(excl_s)}</i>",
            "",
            _line("Сегодня", today_s),
            _line("Вчера", yday_s),
            _line("7 дней", d7_s),
            _line("30 дней", d30_s),
            _line("Всего", all_s),
            "",
            f"• первая: {_fmt_dt(first_at)}",
            f"• последняя: {_fmt_dt(last_at)}",
        ]
    )


async def build_last_campaigns_report_html(pool) -> str:
    campaigns = await fetch_last_campaigns(pool, limit=3)
    return format_last_campaigns_report(campaigns)


async def build_prayer_usage_report_html(pool) -> str:
    return await format_prayer_usage_report(pool)
