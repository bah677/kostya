"""Отчёт по вовлечённости участников (личка ↔ группа ↔ продление)."""

from __future__ import annotations

import html as html_mod
import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from bot.services.llm_call_logger import logged_deepseek_chat
from bot.services.llm_request_kinds import CLUB_ENGAGEMENT_REPORT
from bot.texts.prompts.club_engagement_report import (
    ENGAGEMENT_REPORT_INSIGHTS_SYSTEM,
    build_engagement_report_llm_user_message,
    build_engagement_runtime_context,
)
from config import config

logger = logging.getLogger(__name__)


async def _fetch_engagement_rows(pool, *, report_date: date) -> List[Dict[str, Any]]:
    sql = """
        WITH active AS (
            SELECT l.user_id, l.expires_at
            FROM license l
            WHERE l.status = 'active' AND l.expires_at > NOW()
        ),
        dm AS (
            SELECT m.user_id, COUNT(*) AS dm_msgs
            FROM messages m
            INNER JOIN active a ON a.user_id = m.user_id
            WHERE m.chat_type = 'private'
              AND m.role = 'user'
              AND m.deleted_at IS NULL
              AND (m.created_at AT TIME ZONE 'Europe/Moscow')::date = $1::date
            GROUP BY m.user_id
        ),
        grp AS (
            SELECT m.user_id, COUNT(*) AS grp_msgs
            FROM messages m
            INNER JOIN active a ON a.user_id = m.user_id
            WHERE m.chat_id = $2
              AND m.role = 'user'
              AND m.deleted_at IS NULL
              AND (m.created_at AT TIME ZONE 'Europe/Moscow')::date = $1::date
            GROUP BY m.user_id
        ),
        outreach AS (
            SELECT user_id, proactive_sent_count
            FROM member_outreach_state
            WHERE proactive_sent_date = $1::date
        )
        SELECT
            a.user_id,
            u.first_name,
            u.username,
            COALESCE(d.dm_msgs, 0) AS dm_msgs,
            COALESCE(g.grp_msgs, 0) AS grp_msgs,
            COALESCE(o.proactive_sent_count, 0) AS outreach_sent,
            mp.last_group_activity_at,
            mp.last_dm_at,
            a.expires_at
        FROM active a
        LEFT JOIN users u ON u.user_id = a.user_id
        LEFT JOIN dm d ON d.user_id = a.user_id
        LEFT JOIN grp g ON g.user_id = a.user_id
        LEFT JOIN outreach o ON o.user_id = a.user_id
        LEFT JOIN member_profiles mp ON mp.user_id = a.user_id
        ORDER BY COALESCE(d.dm_msgs, 0) + COALESCE(g.grp_msgs, 0) DESC
    """
    gid = int(config.CLUB_GROUP_ID or 0)
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, report_date, gid)
    return [dict(r) for r in rows]


def _esc(s: Any) -> str:
    return html_mod.escape(str(s or ""))


async def build_engagement_report_html(
    pool,
    user_storage,
    *,
    report_date: Optional[date] = None,
    api_key: str = "",
) -> str:
    ref = report_date or (date.today() - timedelta(days=1))
    rows = await _fetch_engagement_rows(pool, report_date=ref)
    if not rows:
        return f"<b>📊 Engagement · {ref.strftime('%d.%m.%Y')}</b>\n\nНет данных."

    total = len(rows)
    dm_active = sum(1 for r in rows if int(r.get("dm_msgs") or 0) > 0)
    grp_active = sum(1 for r in rows if int(r.get("grp_msgs") or 0) > 0)
    both = sum(
        1
        for r in rows
        if int(r.get("dm_msgs") or 0) > 0 and int(r.get("grp_msgs") or 0) > 0
    )
    silent = sum(
        1
        for r in rows
        if int(r.get("dm_msgs") or 0) == 0 and int(r.get("grp_msgs") or 0) == 0
    )
    outreach_total = sum(int(r.get("outreach_sent") or 0) for r in rows)

    lines = [
        f"<b>📊 Вовлечённость · {ref.strftime('%d.%m.%Y')}</b>",
        "",
        f"Активных лицензий: <b>{total}</b>",
        f"Писали боту в личку: <b>{dm_active}</b> ({100 * dm_active // max(total, 1)}%)",
        f"Писали в группу: <b>{grp_active}</b> ({100 * grp_active // max(total, 1)}%)",
        f"И там и там: <b>{both}</b>",
        f"Молчали везде (за этот день): <b>{silent}</b>",
        f"Проактивных сообщений в личку (рассылка бота): <b>{outreach_total}</b>",
        "",
        "<b>Топ-10 по активности (личка + группа):</b>",
    ]
    for i, r in enumerate(rows[:10], 1):
        name = _esc(r.get("first_name") or r.get("username") or r["user_id"])
        dm = int(r.get("dm_msgs") or 0)
        gr = int(r.get("grp_msgs") or 0)
        lines.append(f"{i}. {name} — личка {dm}, группа {gr}")

    lines.append("")
    lines.append("<b>Молчуны (0+0, до 15):</b>")
    silent_rows = [
        r
        for r in rows
        if int(r.get("dm_msgs") or 0) == 0 and int(r.get("grp_msgs") or 0) == 0
    ][:15]
    if not silent_rows:
        lines.append("— нет")
    else:
        for r in silent_rows:
            name = _esc(r.get("first_name") or r.get("username") or r["user_id"])
            lines.append(f"• {name} (<code>{r['user_id']}</code>)")

    stats_blob = (
        f"лицензий={total}; писали в личку={dm_active}; "
        f"писали в группе={grp_active}; и там и там={both}; "
        f"молчали за день={silent}; проактивных рассылок в личку={outreach_total}"
    )
    key = (api_key or config.DEEPSEEK_API_KEY or "").strip()
    if key and user_storage:
        llm_user = build_engagement_report_llm_user_message(
            report_date_str=ref.strftime("%d.%m.%Y"),
            stats_blob=stats_blob,
            report_excerpt="\n".join(lines[-20:]),
            runtime_context=build_engagement_runtime_context(),
        )
        insights, _ = await logged_deepseek_chat(
            user_storage,
            user_id=0,
            request_kind=CLUB_ENGAGEMENT_REPORT,
            api_key=key,
            system=ENGAGEMENT_REPORT_INSIGHTS_SYSTEM,
            user=llm_user,
            temperature=0.35,
            max_tokens=900,
            timeout_sec=120.0,
        )
        if insights:
            lines.extend(["", "<b>💡 Выводы и рекомендации</b>", insights.strip()])

    return "\n".join(lines)
