"""Воронка эффективности кампаний рассылки (mailing_campaigns).

Когорта: пользователи со статусом ``sent`` в ``mailing_audience``.
Якорь времени: ``sent_at`` (когда сообщение рассылки ушло в личку).

Этапы:
  delivered — получили рассылку (база когорты);
  started   — после рассылки: /start, касание attribution или команда /start в interaction_logs;
  ai_dialog — осмысленное сообщение в личке агенту (не команда);
  ordered   — создан заказ;
  paid      — оплачен заказ; revenue — сумма ``payments.amount_rub`` (succeeded).
"""

from __future__ import annotations

import html as html_mod
import logging
from typing import Any, Dict, List, Optional, Sequence, TYPE_CHECKING

from bot.services.report_exclude import sql_exclude_users

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_FUNNEL_SQL = """
WITH base AS (
    SELECT
        ma.campaign_id,
        ma.user_id,
        ma.sent_at AS anchor_at
    FROM mailing_audience ma
    WHERE ma.campaign_id = ANY($1::int[])
      AND ma.status = 'sent'
      AND ma.sent_at IS NOT NULL
      {exclude_ma}
),
started_u AS (
    SELECT DISTINCT b.campaign_id, b.user_id
    FROM base b
    WHERE EXISTS (
        SELECT 1 FROM attribution_touches at
        WHERE at.user_id = b.user_id
          AND at.created_at >= b.anchor_at
    )
    OR EXISTS (
        SELECT 1 FROM interaction_logs il
        WHERE il.user_id = b.user_id
          AND il.command = '/start'
          AND il.created_at >= b.anchor_at
    )
    OR EXISTS (
        SELECT 1 FROM messages m
        WHERE m.user_id = b.user_id
          AND m.chat_type = 'private'
          AND m.role = 'user'
          AND m.content ILIKE '/start%%'
          AND m.created_at >= b.anchor_at
    )
),
ai_u AS (
    SELECT DISTINCT b.campaign_id, b.user_id
    FROM base b
    WHERE EXISTS (
        SELECT 1 FROM messages m
        WHERE m.user_id = b.user_id
          AND m.chat_type = 'private'
          AND m.role = 'user'
          AND m.created_at >= b.anchor_at
          AND LEFT(TRIM(COALESCE(m.content, '')), 1) <> '/'
    )
),
ord_u AS (
    SELECT DISTINCT b.campaign_id, b.user_id
    FROM base b
    JOIN orders o ON o.user_id = b.user_id AND o.created_at >= b.anchor_at
    {exclude_o}
),
paid_u AS (
    SELECT
        b.campaign_id,
        b.user_id,
        p.amount_rub
    FROM base b
    JOIN orders o ON o.user_id = b.user_id
        AND o.status = 'paid'
        AND o.paid_at >= b.anchor_at
    JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
    {exclude_o}
)
SELECT
    mc.id AS campaign_id,
    mc.name AS campaign_name,
    mc.status AS campaign_status,
    mc.scheduled_at,
    mc.sent_count AS campaign_sent_count,
    COUNT(DISTINCT b.user_id)::int AS delivered,
    COUNT(DISTINCT s.user_id)::int AS started,
    COUNT(DISTINCT a.user_id)::int AS ai_dialog,
    COUNT(DISTINCT ord.user_id)::int AS ordered,
    COUNT(DISTINCT pu.user_id)::int AS paid,
    COALESCE(SUM(pu.amount_rub), 0)::float AS revenue
FROM mailing_campaigns mc
LEFT JOIN base b ON b.campaign_id = mc.id
LEFT JOIN started_u s ON s.campaign_id = b.campaign_id AND s.user_id = b.user_id
LEFT JOIN ai_u a ON a.campaign_id = b.campaign_id AND a.user_id = b.user_id
LEFT JOIN ord_u ord ON ord.campaign_id = b.campaign_id AND ord.user_id = b.user_id
LEFT JOIN paid_u pu ON pu.campaign_id = b.campaign_id AND pu.user_id = b.user_id
WHERE mc.id = ANY($1::int[])
GROUP BY mc.id, mc.name, mc.status, mc.scheduled_at, mc.sent_count
ORDER BY mc.scheduled_at DESC NULLS LAST, mc.id DESC
"""


def _pct(part: int, whole: int) -> str:
    if whole <= 0:
        return "—"
    return f"{100.0 * part / whole:.1f}%"


def _money(rub: float) -> str:
    if rub >= 1_000_000:
        return f"{rub / 1_000_000:.2f} млн ₽"
    if rub >= 1_000:
        return f"{rub / 1_000:.1f} тыс ₽"
    return f"{rub:.0f} ₽"


async def list_recent_campaigns(
    pool: "asyncpg.Pool", *, limit: int = 20
) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, status, scheduled_at, sent_count, failed_count, has_ref_link
            FROM mailing_campaigns
            ORDER BY scheduled_at DESC NULLS LAST, id DESC
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]


async def collect_mailing_funnel(
    pool: "asyncpg.Pool",
    campaign_ids: Sequence[int],
) -> List[Dict[str, Any]]:
    ids = sorted({int(x) for x in campaign_ids if int(x) > 0})
    if not ids:
        return []

    ex_ma, ex_ids = sql_exclude_users("ma.user_id", start_param=2)
    ex_o = ex_ma.replace("ma.user_id", "o.user_id")

    query = _FUNNEL_SQL.format(exclude_ma=ex_ma, exclude_o=ex_o)

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, ids, *ex_ids)
        return [dict(r) for r in rows]


def format_mailing_funnel_html(
    rows: List[Dict[str, Any]],
    *,
    requested_ids: Optional[Sequence[int]] = None,
) -> str:
    if not rows:
        req = ""
        if requested_ids:
            req = f" (id: {', '.join(str(i) for i in requested_ids)})"
        return (
            "<b>📬 Воронка рассылок</b>\n\n"
            f"Нет данных по выбранным кампаниям{req}.\n"
            "Нужны кампании со статусом <code>completed</code> и доставленными "
            "(<code>mailing_audience.status = sent</code>)."
        )

    lines = [
        "<b>📬 Воронка рассылок</b>",
        "<i>Когорта: кому доставлено (sent). События — после "
        "<code>sent_at</code>. Тестировщики исключены.</i>",
        "",
        "Цепочка: <b>доставлено → запуск бота → диалог с агентом → "
        "заказ → оплата</b>",
        "",
    ]

    for row in rows:
        cid = int(row["campaign_id"])
        name = html_mod.escape(str(row.get("campaign_name") or f"#{cid}"))
        status = html_mod.escape(str(row.get("campaign_status") or ""))
        sched = row.get("scheduled_at")
        sched_s = sched.strftime("%d.%m.%Y %H:%M") if sched else "—"

        delivered = int(row.get("delivered") or 0)
        started = int(row.get("started") or 0)
        ai = int(row.get("ai_dialog") or 0)
        ordered = int(row.get("ordered") or 0)
        paid = int(row.get("paid") or 0)
        rev = float(row.get("revenue") or 0)

        lines.append(f"<b>{name}</b> · id <code>{cid}</code> · {status}")
        lines.append(f"📅 {sched_s} · в БД sent_count={int(row.get('campaign_sent_count') or 0)}")
        lines.append(
            f"• {delivered} доставлено → {started} запуск → {ai} диалог → "
            f"{ordered} заказ → {paid} оплата · {_money(rev)}"
        )
        lines.append(
            f"   CR: бот {_pct(started, delivered)} · ИИ {_pct(ai, started)} · "
            f"заказ {_pct(ordered, ai)} · оплата {_pct(paid, ordered)} · "
            f"сквозная {_pct(paid, delivered)}"
        )
        lines.append("")

    lines.append(
        "<i>«Запуск бота» — /start или касание после рассылки. "
        "Персональная ссылка ref_&lt;user_id&gt; не привязана к id кампании; "
        "считаем когорту по факту доставки.</i>"
    )
    return "\n".join(lines)


def format_campaign_catalog_html(campaigns: List[Dict[str, Any]]) -> str:
    lines = [
        "<b>📬 Кампании рассылки</b>",
        "<i>Команда: <code>/mailing_funnel ID [ID …]</code></i>",
        "",
    ]
    if not campaigns:
        lines.append("Кампаний в базе нет.")
        return "\n".join(lines)

    for c in campaigns:
        cid = int(c["id"])
        name = html_mod.escape(str(c.get("name") or ""))
        st = html_mod.escape(str(c.get("status") or ""))
        sent = int(c.get("sent_count") or 0)
        fail = int(c.get("failed_count") or 0)
        ref = "🔗" if c.get("has_ref_link") else ""
        sched = c.get("scheduled_at")
        sched_s = sched.strftime("%d.%m.%Y") if sched else "—"
        lines.append(
            f"<code>{cid}</code> · {name} · {st} · sent {sent} / fail {fail} {ref} · {sched_s}"
        )
    return "\n".join(lines)
