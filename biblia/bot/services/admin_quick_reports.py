"""Быстрые «сейчас»-отчёты для /adm."""

from __future__ import annotations

import html as html_mod
import logging
from datetime import datetime
from typing import Any, Dict, List
from zoneinfo import ZoneInfo


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


async def build_last_campaigns_report_html(pool) -> str:
    campaigns = await fetch_last_campaigns(pool, limit=3)
    return format_last_campaigns_report(campaigns)


async def build_prayer_usage_report_html(pool) -> str:
    """Кнопка /adm «Статистика молитв» → обзор за 30 дней (см. prayer_usage_report)."""
    from bot.services.prayer_usage_report import build_prayer_stats_html

    return await build_prayer_stats_html(pool, screen="ov", period="30")
