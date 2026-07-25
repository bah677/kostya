"""Выборки диалогов biblia для mining."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List

import asyncpg

from collectors.windows import day_window

logger = logging.getLogger(__name__)

_USER_MSG_FILTER = """
(
  m.role = 'user'
  OR (COALESCE(m.sender_type, '') = 'user' AND COALESCE(m.role, '') NOT IN ('assistant', 'bot'))
)
"""


async def sample_dialog_threads(
    biblia: asyncpg.Pool,
    day: date,
    *,
    limit: int = 80,
) -> List[Dict[str, Any]]:
    """
    Берём до `limit` user_id, активных в день, с коротким хвостом сообщений.
    Классификация грубая: donated / short(затух) / long.
    """
    w = day_window(day)
    async with biblia.acquire() as conn:
        users = await conn.fetch(
            f"""
            SELECT m.user_id, COUNT(*) AS msg_cnt
            FROM messages m
            WHERE {_USER_MSG_FILTER}
              AND m.created_at >= $1 AND m.created_at < $2
              AND COALESCE(TRIM(m.content), '') <> ''
            GROUP BY m.user_id
            ORDER BY msg_cnt DESC
            LIMIT $3
            """,
            w.start_utc,
            w.end_utc,
            limit,
        )
        if not users:
            return []

        uids = [int(r["user_id"]) for r in users]
        donated = await conn.fetch(
            """
            SELECT DISTINCT user_id
            FROM payments
            WHERE status = 'succeeded'
              AND order_id IS NULL
              AND created_at >= $1 AND created_at < $2
              AND user_id = ANY($3::bigint[])
            """,
            w.start_utc,
            w.end_utc,
            uids,
        )
        donated_set = {int(r["user_id"]) for r in donated}

        # хвост сообщений за день + чуть раньше для контекста
        from datetime import timedelta

        ctx_start = w.start_utc - timedelta(hours=6)
        msgs = await conn.fetch(
            """
            SELECT user_id, content, role, sender_type, created_at
            FROM messages
            WHERE user_id = ANY($1::bigint[])
              AND created_at >= $2
              AND created_at < $3
              AND COALESCE(TRIM(content), '') <> ''
            ORDER BY user_id, created_at
            """,
            uids,
            ctx_start,
            w.end_utc,
        )

    by_user: Dict[int, List[Dict[str, Any]]] = {u: [] for u in uids}
    for m in msgs:
        uid = int(m["user_id"])
        if uid not in by_user:
            continue
        role = (m["role"] or m["sender_type"] or "?").lower()
        text = (m["content"] or "").replace("\n", " ").strip()
        if len(text) > 280:
            text = text[:279] + "…"
        by_user[uid].append({"role": role, "text": text})

    threads: List[Dict[str, Any]] = []
    for r in users:
        uid = int(r["user_id"])
        cnt = int(r["msg_cnt"])
        bucket = "donated" if uid in donated_set else ("short" if cnt <= 2 else "active")
        lines = by_user.get(uid) or []
        # truncate thread
        if len(lines) > 12:
            lines = lines[:4] + [{"role": "…", "text": f"({len(lines)-8} msgs skipped)"}] + lines[-4:]
        threads.append(
            {
                "user_id": uid,
                "msg_cnt": cnt,
                "bucket": bucket,
                "messages": lines,
            }
        )
    return threads


def format_threads_blob(threads: List[Dict[str, Any]], max_chars: int = 12000) -> str:
    parts: List[str] = []
    for t in threads:
        header = f"--- user={t['user_id']} bucket={t['bucket']} msgs={t['msg_cnt']} ---"
        body = "\n".join(f"{m['role']}: {m['text']}" for m in t["messages"])
        parts.append(f"{header}\n{body}")
    blob = "\n\n".join(parts)
    if len(blob) > max_chars:
        return blob[: max_chars - 1] + "…"
    return blob


async def theme_keyword_stats(
    biblia: asyncpg.Pool, day: date
) -> Dict[str, int]:
    """Грубая эвристика тем по ключевым словам в user-сообщениях дня."""
    w = day_window(day)
    themes = {
        "anxiety_fear": r"(тревог|страх|паник|волн|беспокой)",
        "love_relations": r"(любов|отношен|брак|семь|муж|жен)",
        "history_bible": r"(истори|апостол|евангел|завет|пророк)",
        "prayer": r"(молитв|помол)",
        "health": r"(болезн|здоров|исцел)",
    }
    out: Dict[str, int] = {}
    async with biblia.acquire() as conn:
        for name, pattern in themes.items():
            n = int(
                await conn.fetchval(
                    f"""
                    SELECT COUNT(DISTINCT m.user_id)
                    FROM messages m
                    WHERE {_USER_MSG_FILTER}
                      AND m.created_at >= $1 AND m.created_at < $2
                      AND m.content ~* $3
                    """,
                    w.start_utc,
                    w.end_utc,
                    pattern,
                )
                or 0
            )
            out[name] = n
    return out
