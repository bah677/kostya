#!/usr/bin/env python3
"""
Бэкфилл: лиды на status 103 (холод закончен), но с диалогом → status 120 (stuck_dialog).

Ставит last_assistant_at так, чтобы пинги ушли в окне ~3 ч:
  • первые — сразу (якорь = now − 24 ч, delay пинга 120 = 1440 мин в followup_messages);
  • последние из 200 — примерно через 3 ч.

Требования на проде:
  • бот запущен, FOLLOWUP_STUCK_DIALOG_ENABLED=true;
  • цикл followup раз в ~60 с обрабатывает status 120.

Запуск:
  python3 scripts/backfill_followup_stuck_120.py --dry-run
  python3 scripts/backfill_followup_stuck_120.py --apply --limit 200
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.services.report_exclude import report_exclude_user_ids
from config import config
from storage.user_storage import UserStorage

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_stuck_120")

# Должно совпадать с FollowupFeature.STUCK_DELAY_MIN_MINUTES и followup_messages.delay для 120
STUCK_PING_DELAY_MINUTES = 1440
SPREAD_HOURS = 3
STATUS_WAITING_STUCK = 120
SEG_STUCK_DIALOG = "stuck_dialog"

_CANDIDATES_SQL = """
WITH base AS (
    SELECT
        u.user_id,
        fs.status,
        fs.segment,
        fs.started_at AS fs_started_at,
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
        (
            SELECT COUNT(*)::int
            FROM messages m
            WHERE m.user_id = u.user_id
              AND m.chat_type = 'private'
              AND m.role = 'user'
              AND m.deleted_at IS NULL
              AND COALESCE(m.message_type, '') <> 'callback'
              AND m.content IS NOT NULL
              AND TRIM(m.content) <> ''
              AND m.content NOT ILIKE '/start%%'
              AND LENGTH(TRIM(m.content)) > 2
        ) AS meaningful_count,
        (
            SELECT COUNT(*)::int
            FROM messages m
            WHERE m.user_id = u.user_id
              AND m.chat_type = 'private'
              AND m.role = 'assistant'
              AND m.deleted_at IS NULL
        ) AS assistant_count,
        EXISTS (
            SELECT 1 FROM orders o
            LEFT JOIN payments p ON o.id = p.order_id AND p.status = 'succeeded'
            WHERE o.user_id = u.user_id
              AND o.status = 'pending'
              AND (p.id IS NULL OR p.status IS DISTINCT FROM 'succeeded')
        ) AS has_unpaid,
        EXISTS (
            SELECT 1 FROM messages m
            WHERE m.user_id = u.user_id
              AND m.chat_type = 'private'
              AND m.role = 'user'
              AND m.deleted_at IS NULL
              AND (
                m.content ~* '(^|\\s)(нет|стоп)(\\s|$|!)'
                OR m.content ILIKE '%%не интерес%%'
                OR m.content ILIKE '%%отстан%%'
                OR m.content ILIKE '%%пока нет%%'
              )
        ) AS has_refusal
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
    SELECT *,
        CASE
            WHEN status IN (901, 997, 998) THEN 'final'
            WHEN has_unpaid THEN 'cart'
            WHEN has_dialog THEN 'dialog'
            ELSE 'cold'
        END AS chain
    FROM base
),
last_msg AS (
    SELECT DISTINCT ON (m.user_id)
        m.user_id,
        m.role AS last_role,
        m.created_at AS last_msg_at
    FROM messages m
    INNER JOIN tagged t ON t.user_id = m.user_id
    WHERE m.chat_type = 'private'
      AND m.deleted_at IS NULL
      AND COALESCE(m.message_type, '') <> 'callback'
      AND TRIM(COALESCE(m.content, '')) <> ''
    ORDER BY m.user_id, m.created_at DESC, m.id DESC
)
SELECT
    t.user_id,
    t.status AS old_status,
    t.segment AS old_segment,
    t.fs_started_at,
    lm.last_msg_at,
    t.meaningful_count,
    t.assistant_count
FROM tagged t
JOIN last_msg lm ON lm.user_id = t.user_id
WHERE t.chain = 'dialog'
  AND t.status IN (101, 102, 103)
  AND t.meaningful_count >= 2
  AND t.assistant_count >= 1
  AND NOT t.has_refusal
  AND lm.last_role = 'assistant'
  AND t.status NOT IN (120, 121, 122)
ORDER BY
    t.status DESC,
    t.fs_started_at ASC NULLS LAST,
    t.user_id ASC
LIMIT ${limit_ph}
"""


async def fetch_candidates(
    storage: UserStorage, limit: int
) -> List[dict]:
    exclude_sql, exclude_ids = _exclude_fragment()
    limit_ph = len(exclude_ids) + 1
    sql = _CANDIDATES_SQL.format(exclude_u=exclude_sql, limit_ph=limit_ph)
    async with storage.get_connection() as conn:
        rows = await conn.fetch(sql, *exclude_ids, limit)
    return [dict(r) for r in rows]


def _exclude_fragment() -> Tuple[str, list]:
    ids = list(report_exclude_user_ids())
    if not ids:
        return "", []
    ph = ", ".join(f"${i + 1}" for i in range(len(ids)))
    return f" AND u.user_id NOT IN ({ph})", ids


def _anchor_times(n: int, *, spread_hours: float) -> List[datetime]:
    """UTC naive — база для started_at и last_assistant_at."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    base_anchor = now - timedelta(minutes=STUCK_PING_DELAY_MINUTES)
    if n <= 1:
        return [base_anchor]
    step_sec = (spread_hours * 3600) / (n - 1)
    return [base_anchor + timedelta(seconds=i * step_sec) for i in range(n)]


def _started_at_value(anchor: datetime) -> datetime:
    """followup_states.started_at = timestamp without time zone."""
    if anchor.tzinfo is not None:
        return anchor.astimezone(timezone.utc).replace(tzinfo=None)
    return anchor


def _last_assistant_at_value(anchor: datetime) -> datetime:
    """followup_states.last_assistant_at = timestamptz."""
    if anchor.tzinfo is None:
        return anchor.replace(tzinfo=timezone.utc)
    return anchor.astimezone(timezone.utc)


async def apply_updates(
    storage: UserStorage,
    rows: List[dict],
    anchors: List[datetime],
) -> int:
    updated = 0
    async with storage.get_connection() as conn:
        async with conn.transaction():
            for row, anchor in zip(rows, anchors):
                await conn.execute(
                    """
                    UPDATE followup_states
                    SET status = $2,
                        segment = $3,
                        started_at = $4::timestamp,
                        last_assistant_at = $5::timestamptz,
                        stuck_context = NULL,
                        last_topic = NULL,
                        updated_at = NOW()
                    WHERE user_id = $1
                    """,
                    row["user_id"],
                    STATUS_WAITING_STUCK,
                    SEG_STUCK_DIALOG,
                    _started_at_value(anchor),
                    _last_assistant_at_value(anchor),
                )
                updated += 1
    return updated


async def run(
    *,
    dry_run: bool,
    limit: int,
    spread_hours: float,
) -> None:
    storage = UserStorage(config.database_url)
    await storage.initialize()

    rows = await fetch_candidates(storage, limit)
    if not rows:
        logger.info("Кандидатов не найдено.")
        await storage.close()
        return

    anchors = _anchor_times(len(rows), spread_hours=spread_hours)
    now = datetime.now(timezone.utc)

    logger.info("Кандидатов: %s (limit=%s)", len(rows), limit)
    logger.info(
        "Пинг 120: delay=%s мин, размазано на ~%.1f ч",
        STUCK_PING_DELAY_MINUTES,
        spread_hours,
    )
    logger.info(
        "Первый eligible сразу (anchor=%s), последний ~%s",
        anchors[0].isoformat(),
        anchors[-1].isoformat(),
    )

    by_old = {}
    for r in rows:
        by_old[r["old_status"]] = by_old.get(r["old_status"], 0) + 1
    logger.info("По старому status: %s", by_old)

    if dry_run:
        for i, (r, a) in enumerate(list(zip(rows, anchors))[:5]):
            mins_until = STUCK_PING_DELAY_MINUTES - (now.replace(tzinfo=None) - a).total_seconds() / 60
            logger.info(
                "  sample uid=%s old=%s anchor=%s eligible_in_min=%.0f",
                r["user_id"],
                r["old_status"],
                a.isoformat(),
                max(0, mins_until),
            )
        if len(rows) > 5:
            logger.info("  … и ещё %s", len(rows) - 5)
        logger.info("DRY-RUN: UPDATE не выполнен.")
        await storage.close()
        return

    n = await apply_updates(storage, rows, anchors)
    logger.info("Обновлено записей followup_states: %s → status 120", n)
    logger.info(
        "Дальше бот сам: _prepare_stuck_context (LLM) + отправка пинга на цикле followup (~60 с)."
    )
    await storage.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Backfill stuck_dialog status 120")
    p.add_argument("--dry-run", action="store_true", help="Только показать выборку")
    p.add_argument("--apply", action="store_true", help="Записать в БД")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument(
        "--spread-hours",
        type=float,
        default=SPREAD_HOURS,
        help="Размазать якоря last_assistant_at на N часов (первые пинги сразу)",
    )
    args = p.parse_args()
    if not args.dry_run and not args.apply:
        p.error("Укажите --dry-run или --apply")
    asyncio.run(
        run(
            dry_run=args.dry_run,
            limit=args.limit,
            spread_hours=args.spread_hours,
        )
    )


if __name__ == "__main__":
    main()
