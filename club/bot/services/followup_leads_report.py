"""Срез лидов без лицензии: 3 цепочки × 2 фазы + финалы (по поведению)."""

from __future__ import annotations

import html as html_mod
import logging
from dataclasses import dataclass
from typing import List, Optional

from bot.services.report_exclude import sql_exclude_users

logger = logging.getLogger(__name__)

# 999 (блок) в отчёт не входит
_FINAL_STATUSES = (901, 997, 998)

_FINAL_LABELS = {
    901: "оплатили",
    997: "тяжёлая тема",
    998: "отказ",
}

_REPORT_SQL = """
WITH base AS (
    SELECT
        u.user_id,
        fs.status,
        EXISTS (
            SELECT 1 FROM messages m
            WHERE m.user_id = u.user_id
              AND m.chat_type = 'private'
              AND m.role = 'user'
              AND m.deleted_at IS NULL
              AND COALESCE(m.message_type, '') <> 'callback'
              AND m.content IS NOT NULL
              AND TRIM(m.content) <> ''
              AND m.content NOT ILIKE '/start%%'
              AND LENGTH(TRIM(m.content)) > 2
        ) AS has_dialog,
        EXISTS (
            SELECT 1 FROM orders o
            LEFT JOIN payments p ON o.id = p.order_id AND p.status = 'succeeded'
            WHERE o.user_id = u.user_id
              AND o.status = 'pending'
              AND (p.id IS NULL OR p.status IS DISTINCT FROM 'succeeded')
        ) AS has_unpaid
    FROM users u
    INNER JOIN followup_states fs ON fs.user_id = u.user_id
    WHERE u.is_active IS TRUE
      AND fs.status NOT IN (0, 999)
      AND NOT EXISTS (
        SELECT 1 FROM license l
        WHERE l.user_id = u.user_id
          AND l.status = 'active'
          AND l.expires_at > NOW()
      )
    {exclude_u}
),
tagged AS (
    SELECT
        status,
        CASE
            WHEN status IN (901, 997, 998) THEN 'final'
            WHEN has_unpaid THEN 'cart'
            WHEN has_dialog THEN 'dialog'
            ELSE 'cold'
        END AS chain
    FROM base
)
SELECT
    COUNT(*) FILTER (WHERE chain = 'cold' AND status = 103)::int AS cold_done,
    COUNT(*) FILTER (WHERE chain = 'cold' AND status IN (101, 102))::int AS cold_progress,
    COUNT(*) FILTER (WHERE chain = 'dialog' AND status IN (112, 122))::int AS dialog_done,
    COUNT(*) FILTER (
        WHERE chain = 'dialog'
          AND status IN (101, 102, 103, 110, 111, 120, 121)
    )::int AS dialog_progress,
    COUNT(*) FILTER (
        WHERE chain = 'dialog' AND status IN (110, 111, 120, 121)
    )::int AS dialog_progress_active,
    COUNT(*) FILTER (
        WHERE chain = 'dialog' AND status = 103
    )::int AS dialog_progress_legacy_103,
    COUNT(*) FILTER (
        WHERE chain = 'dialog' AND status IN (101, 102)
    )::int AS dialog_progress_legacy_101_102,
    COUNT(*) FILTER (WHERE chain = 'cart' AND status = 203)::int AS cart_done,
    COUNT(*) FILTER (WHERE chain = 'cart' AND status <> 203)::int AS cart_progress,
    COUNT(*) FILTER (WHERE chain = 'final')::int AS finals_total,
    COUNT(*) FILTER (WHERE chain = 'final' AND status = 901)::int AS final_901,
    COUNT(*) FILTER (WHERE chain = 'final' AND status = 997)::int AS final_997,
    COUNT(*) FILTER (WHERE chain = 'final' AND status = 998)::int AS final_998,
    COUNT(*) FILTER (
        WHERE chain = 'cold' AND status NOT IN (101, 102, 103)
    )::int AS cold_other_status,
    COUNT(*) FILTER (
        WHERE chain = 'dialog' AND status NOT IN (
            101, 102, 103, 110, 111, 112, 120, 121, 122
        )
    )::int AS dialog_other_status,
    COUNT(*) FILTER (
        WHERE chain = 'cart' AND status NOT IN (201, 202, 203)
    )::int AS cart_other_status
FROM tagged
"""


@dataclass(frozen=True)
class FollowupLeadsReport:
    cold_done: int
    cold_progress: int
    dialog_done: int
    dialog_progress: int
    dialog_progress_active: int
    dialog_progress_legacy_103: int
    dialog_progress_legacy_101_102: int
    cart_done: int
    cart_progress: int
    finals_total: int
    final_901: int
    final_997: int
    final_998: int
    cold_other_status: int
    dialog_other_status: int
    cart_other_status: int


async def collect_followup_leads_report(pool) -> FollowupLeadsReport:
    exclude_sql, exclude_ids = sql_exclude_users("u.user_id")
    sql = _REPORT_SQL.format(exclude_u=exclude_sql)
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, *exclude_ids)
    except Exception as e:
        logger.exception("followup_leads_report query failed: %s", e)
        raise

    if not row:
        return FollowupLeadsReport(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    return FollowupLeadsReport(
        cold_done=int(row["cold_done"] or 0),
        cold_progress=int(row["cold_progress"] or 0),
        dialog_done=int(row["dialog_done"] or 0),
        dialog_progress=int(row["dialog_progress"] or 0),
        dialog_progress_active=int(row["dialog_progress_active"] or 0),
        dialog_progress_legacy_103=int(row["dialog_progress_legacy_103"] or 0),
        dialog_progress_legacy_101_102=int(row["dialog_progress_legacy_101_102"] or 0),
        cart_done=int(row["cart_done"] or 0),
        cart_progress=int(row["cart_progress"] or 0),
        finals_total=int(row["finals_total"] or 0),
        final_901=int(row["final_901"] or 0),
        final_997=int(row["final_997"] or 0),
        final_998=int(row["final_998"] or 0),
        cold_other_status=int(row["cold_other_status"] or 0),
        dialog_other_status=int(row["dialog_other_status"] or 0),
        cart_other_status=int(row["cart_other_status"] or 0),
    )


def format_followup_leads_block(
    report: FollowupLeadsReport, *, for_daily: bool = False
) -> str:
    """Краткий блок: 6 чисел + финалы (без status 999)."""
    six = (
        report.cold_done
        + report.cold_progress
        + report.dialog_done
        + report.dialog_progress
        + report.cart_done
        + report.cart_progress
    )
    other = (
        report.cold_other_status
        + report.dialog_other_status
        + report.cart_other_status
    )
    grand = six + report.finals_total + other

    if grand == 0:
        return "<b>📋 Срез дожима лидов</b>\n<i>Нет данных.</i>"

    lines: List[str] = [
        "<b>📋 Срез дожима лидов</b>",
        "",
        f"👥 Всего: <b>{grand}</b>",
        "",
        "<b>1. Ни разу не писали боту</b>",
        f"  Цепочка закончена: <b>{report.cold_done}</b>",
        f"  В процессе: <b>{report.cold_progress}</b>",
        "",
        "<b>2. Писали боту и пропали</b>",
        f"  Цепочка закончена: <b>{report.dialog_done}</b>",
        f"  В процессе: <b>{report.dialog_progress}</b>",
        f"    · живая очередь (110–121): <b>{report.dialog_progress_active}</b>",
        f"    · легаси, конец холода (103): <b>{report.dialog_progress_legacy_103}</b>",
    ]
    if report.dialog_progress_legacy_101_102:
        lines.append(
            f"    · легаси, ещё 101–102: <b>{report.dialog_progress_legacy_101_102}</b>"
        )
    lines.extend(
        [
            "",
            "<b>3. Заказ без оплаты</b>",
            f"  Цепочка закончена: <b>{report.cart_done}</b>",
            f"  В процессе: <b>{report.cart_progress}</b>",
        ]
    )

    if other and not for_daily:
        lines.append(f"\n<i>Прочее: {other}</i>")

    if report.finals_total:
        lines.extend(
            [
                "",
                "<b>⏹ Финалы</b>",
                f"  Всего: <b>{report.finals_total}</b>",
            ]
        )
        for code in _FINAL_STATUSES:
            n = {901: report.final_901, 997: report.final_997, 998: report.final_998}[
                code
            ]
            if n:
                lines.append(
                    f"  · <b>{code}</b> {html_mod.escape(_FINAL_LABELS[code])}: <b>{n}</b>"
                )

    return "\n".join(lines)


def format_followup_leads_html(report: FollowupLeadsReport) -> str:
    """Команда /followup_leads в личку."""
    body = format_followup_leads_block(report)
    return f"{body}\n\n<i>/followup_leads</i>"
