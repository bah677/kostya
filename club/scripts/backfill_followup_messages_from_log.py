#!/usr/bin/env python3
"""
Бэкфилл: доставленные строки followup_log → messages (история для агента).

Для каждой записи followup_log (delivered=true), у которой нет assistant-сообщения
в окне ±3 мин от sent_at и нет уже синтетической строки с backfill_followup_log_id,
вставляет синтетическое исходящее:
  • текст из followup_messages (по message_id или status);
  • для status 120/121 — ping_line из followup_states.stuck_context или last_topic;
  • персонализация {имя}, {тема} как в FollowupFeature.

telegram_message_id отрицательный (-1_000_000_000 - log.id) — не конфликтует с Telegram.

Запуск:
  python3 scripts/backfill_followup_messages_from_log.py --dry-run
  python3 scripts/backfill_followup_messages_from_log.py --apply --since 2026-05-22
  python3 scripts/backfill_followup_messages_from_log.py --apply --user-id 265228459
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.followup_segments import pick_topic_snippet
from bot.services.report_exclude import report_exclude_user_ids
from bot.utils.telegram_html import strip_subscribe_cta
from config import config
from storage.user_storage import UserStorage

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_followup_messages")

AI_GENERATE_MARKER = "__AI_GENERATE__"
SYNTHETIC_TG_ID_BASE = -1_000_000_000

# В followup_messages нет отдельных шаблонов — тот же текст, что у «родительского» статуса.
TEMPLATE_STATUS_FALLBACK: Dict[int, int] = {
    121: 120,  # stuck: пинг отправлен
    111: 110,  # engaged: напоминание отправлено
}

_CANDIDATES_SQL = """
SELECT fl.id, fl.user_id, fl.status, fl.message_id AS fm_row_id, fl.sent_at
FROM followup_log fl
WHERE fl.delivered IS TRUE
  {since_clause}
  {user_clause}
  AND NOT EXISTS (
    SELECT 1 FROM messages m
    WHERE m.user_id = fl.user_id
      AND m.role = 'assistant'
      AND m.sender_type = 'bot'
      AND m.deleted_at IS NULL
      AND m.created_at >= fl.sent_at - INTERVAL '90 seconds'
      AND m.created_at <= fl.sent_at + INTERVAL '3 minutes'
  )
  AND NOT EXISTS (
    SELECT 1 FROM messages m
    WHERE m.metadata->>'backfill_followup_log_id' = fl.id::text
  )
  {exclude_u}
ORDER BY fl.sent_at ASC
LIMIT ${limit_ph}
"""


def _personalize(template: str, first_name: Optional[str]) -> str:
    name = (first_name or "").strip() or "друг"
    return template.replace("{имя}", html.escape(name))


def _personalize_with_topic(
    template: str, first_name: Optional[str], topic: Optional[str]
) -> str:
    text = _personalize(template, first_name)
    snippet = pick_topic_snippet(topic or "") or "ваш вопрос"
    return text.replace("{тема}", html.escape(snippet))


def _personalize_stuck(
    template: str,
    first_name: Optional[str],
    stuck_context: Optional[Dict[str, Any]],
) -> str:
    topic = "ваш вопрос"
    if stuck_context:
        analysis = stuck_context.get("analysis") or {}
        topic = (
            stuck_context.get("ping_line")
            or analysis.get("ping_line")
            or analysis.get("topic_label")
            or topic
        )
    return _personalize_with_topic(template, first_name, topic)


def _resolve_template(
    row: dict,
    by_id: Dict[int, str],
    by_status: Dict[int, str],
) -> Optional[str]:
    fm_id = row.get("fm_row_id")
    if fm_id and int(fm_id) in by_id:
        return by_id[int(fm_id)]
    st = int(row["status"])
    tpl_st = TEMPLATE_STATUS_FALLBACK.get(st, st)
    return by_status.get(tpl_st)


async def fetch_templates(storage: UserStorage) -> Tuple[Dict[int, str], Dict[int, str]]:
    async with storage.get_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT id, status, message_text
            FROM followup_messages
            WHERE is_active IS TRUE
            """
        )
    by_id: Dict[int, str] = {}
    by_status: Dict[int, str] = {}
    for r in rows:
        by_id[int(r["id"])] = r["message_text"]
        by_status[int(r["status"])] = r["message_text"]
    return by_id, by_status


async def fetch_stuck_meta(
    storage: UserStorage, user_ids: List[int]
) -> Dict[int, Dict[str, Any]]:
    if not user_ids:
        return {}
    async with storage.get_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT user_id, stuck_context, last_topic
            FROM followup_states
            WHERE user_id = ANY($1::bigint[])
            """,
            user_ids,
        )
    out: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        sc = r["stuck_context"]
        if isinstance(sc, str):
            try:
                sc = json.loads(sc)
            except json.JSONDecodeError:
                sc = None
        out[int(r["user_id"])] = {
            "stuck_context": sc if isinstance(sc, dict) else None,
            "last_topic": r["last_topic"],
        }
    return out


async def fetch_users(
    storage: UserStorage, user_ids: List[int]
) -> Dict[int, Optional[str]]:
    if not user_ids:
        return {}
    async with storage.get_connection() as conn:
        rows = await conn.fetch(
            "SELECT user_id, first_name FROM users WHERE user_id = ANY($1::bigint[])",
            user_ids,
        )
    return {int(r["user_id"]): r["first_name"] for r in rows}


async def fetch_candidates(
    storage: UserStorage,
    *,
    since: Optional[datetime],
    user_id: Optional[int],
    limit: int,
) -> List[dict]:
    since_clause = ""
    user_clause = ""
    args: list = []
    n = 0

    if since:
        n += 1
        since_clause = f" AND fl.sent_at >= ${n}"
        args.append(since)
    if user_id is not None:
        n += 1
        user_clause = f" AND fl.user_id = ${n}"
        args.append(user_id)

    exclude_sql, exclude_ids = _exclude_fragment(n)
    n += len(exclude_ids)
    limit_ph = n + 1
    sql = _CANDIDATES_SQL.format(
        since_clause=since_clause,
        user_clause=user_clause,
        exclude_u=exclude_sql,
        limit_ph=limit_ph,
    )
    args.extend(exclude_ids)
    args.append(limit)

    async with storage.get_connection() as conn:
        rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


def _exclude_fragment(param_offset: int) -> Tuple[str, list]:
    ids = list(report_exclude_user_ids())
    if not ids:
        return "", []
    ph = ", ".join(
        f"${param_offset + i}" for i in range(1, len(ids) + 1)
    )
    return f" AND fl.user_id NOT IN ({ph})", ids


def _sent_at_timestamptz(sent_at: datetime) -> datetime:
    if sent_at.tzinfo is None:
        return sent_at.replace(tzinfo=timezone.utc)
    return sent_at.astimezone(timezone.utc)


def _build_content(
    row: dict,
    template: str,
    first_name: Optional[str],
    stuck_meta: Dict[int, Dict[str, Any]],
) -> str:
    status = int(row["status"])
    uid = int(row["user_id"])
    meta = stuck_meta.get(uid) or {}

    if status in (120, 121):
        text = _personalize_stuck(
            template, first_name, meta.get("stuck_context")
        )
    elif status in (110, 111):
        text = _personalize_with_topic(
            template, first_name, meta.get("last_topic")
        )
    else:
        text = _personalize(template, first_name)

    body, _ = strip_subscribe_cta(text)
    return body.strip()


async def insert_message(
    conn,
    *,
    log_id: int,
    user_id: int,
    status: int,
    content: str,
    sent_at: datetime,
) -> int:
    created = _sent_at_timestamptz(sent_at)
    tg_mid = SYNTHETIC_TG_ID_BASE - log_id
    meta = {
        "source": "followup",
        "backfill_followup_log_id": log_id,
        "followup_status": status,
        "synthetic": True,
        "backfill_note": "from followup_log + template/stuck_context",
    }
    return await conn.fetchval(
        """
        INSERT INTO messages (
            user_id, role, content, created_at,
            telegram_message_id, chat_id, chat_type,
            sender_type, message_type, subtype, metadata
        ) VALUES (
            $1, 'assistant', $2, $3::timestamptz,
            $4, $5, 'private',
            'bot', 'text', $6, $7::jsonb
        )
        RETURNING id
        """,
        user_id,
        content,
        created,
        tg_mid,
        user_id,
        str(status),
        json.dumps(meta, ensure_ascii=False),
    )


async def run(
    *,
    dry_run: bool,
    since: Optional[datetime],
    user_id: Optional[int],
    limit: int,
) -> None:
    storage = UserStorage(config.database_url)
    await storage.initialize()

    by_id, by_status = await fetch_templates(storage)
    rows = await fetch_candidates(
        storage, since=since, user_id=user_id, limit=limit
    )
    if not rows:
        logger.info("Нет строк для бэкфилла.")
        await storage.close()
        return

    uids = list({int(r["user_id"]) for r in rows})
    users = await fetch_users(storage, uids)
    stuck_meta = await fetch_stuck_meta(storage, uids)

    inserted = 0
    skipped_ai = 0
    skipped_empty = 0
    samples: List[str] = []

    async with storage.get_connection() as conn:
        for row in rows:
            template = _resolve_template(row, by_id, by_status)
            if not template:
                logger.warning(
                    "skip log_id=%s: нет шаблона status=%s",
                    row["id"],
                    row["status"],
                )
                continue
            if AI_GENERATE_MARKER in template:
                skipped_ai += 1
                continue

            content = _build_content(
                row, template, users.get(int(row["user_id"])), stuck_meta
            )
            if not content:
                skipped_empty += 1
                continue

            if dry_run:
                inserted += 1
                if len(samples) < 5:
                    samples.append(
                        f"log={row['id']} uid={row['user_id']} st={row['status']} "
                        f"at={row['sent_at']}: {content[:70]}…"
                    )
                continue

            mid = await insert_message(
                conn,
                log_id=int(row["id"]),
                user_id=int(row["user_id"]),
                status=int(row["status"]),
                content=content,
                sent_at=row["sent_at"],
            )
            inserted += 1
            if len(samples) < 5:
                samples.append(
                    f"log={row['id']} → messages.id={mid} uid={row['user_id']}"
                )

    logger.info("Кандидатов из followup_log: %s", len(rows))
    logger.info(
        "Будет/вставлено: %s, пропуск AI-шаблон: %s, пустой текст: %s",
        inserted,
        skipped_ai,
        skipped_empty,
    )
    for s in samples:
        logger.info("  %s", s)
    if dry_run:
        logger.info("DRY-RUN: INSERT не выполнен.")
    await storage.close()


def _parse_since(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"Неверная дата: {value}")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Backfill messages from followup_log"
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--apply", action="store_true")
    p.add_argument(
        "--since",
        type=_parse_since,
        help="Минимальный sent_at (YYYY-MM-DD)",
    )
    p.add_argument("--user-id", type=int, default=None)
    p.add_argument("--limit", type=int, default=5000)
    args = p.parse_args()
    if not args.dry_run and not args.apply:
        p.error("Укажите --dry-run или --apply")

    asyncio.run(
        run(
            dry_run=args.dry_run,
            since=args.since,
            user_id=args.user_id,
            limit=args.limit,
        )
    )


if __name__ == "__main__":
    main()
