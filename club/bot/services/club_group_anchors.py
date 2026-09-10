"""Якоря на недавние сообщения клубной группы (для шагов первой недели)."""

from __future__ import annotations

import logging
from typing import List, Optional

from bot.services.club_greeter_service import club_message_deep_link

logger = logging.getLogger(__name__)


async def fetch_recent_group_anchors(
    pool,
    *,
    club_group_id: int,
    limit: int = 3,
    exclude_topic_id: int = 0,
    lookback_hours: int = 36,
) -> List[str]:
    """До `limit` deep-link на самые длинные недавние сообщения людей."""
    if not pool or not club_group_id:
        return []
    topic_filter = ""
    args: list = [club_group_id, lookback_hours]
    n = 3
    if exclude_topic_id > 0:
        topic_filter = (
            f" AND COALESCE((m.metadata->>'message_thread_id')::bigint, 0) <> ${n}"
        )
        args.append(exclude_topic_id)
        n += 1
    args.append(limit)
    sql = f"""
        SELECT m.telegram_message_id, m.content, m.metadata
        FROM messages m
        WHERE m.chat_id = $1
          AND m.role = 'user'
          AND m.deleted_at IS NULL
          AND COALESCE(TRIM(m.content), '') <> ''
          AND m.telegram_message_id IS NOT NULL
          AND m.created_at > NOW() - make_interval(hours => $2)
          {topic_filter}
        ORDER BY length(m.content) DESC
        LIMIT ${n}
    """
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
    except Exception as e:
        logger.warning("fetch_recent_group_anchors: %s", e)
        return []

    anchors: List[str] = []
    for r in rows:
        mid = int(r["telegram_message_id"] or 0)
        if mid <= 0:
            continue
        thr = r.get("metadata") or {}
        if isinstance(thr, str):
            thr = {}
        thread_id: Optional[int] = None
        try:
            thread_id = int((thr or {}).get("message_thread_id") or 0) or None
        except Exception:
            thread_id = None
        anchors.append(
            club_message_deep_link(
                chat_id=int(club_group_id),
                message_id=mid,
                thread_id=thread_id,
            )
        )
    return anchors


def format_anchors_for_hint(anchors: List[str]) -> str:
    if not anchors:
        return "Ссылки на сообщения группы пока нет — дай общую ссылку в группу, если есть в контексте."
    lines = "\n".join(f"- {a}" for a in anchors)
    return (
        "Используй ровно одну из этих ссылок на сообщения группы "
        f"(предпочтительно первую):\n{lines}"
    )


async def fetch_recent_group_topic_snippet(
    pool,
    *,
    club_group_id: int,
    exclude_topic_id: int = 0,
    lookback_hours: int = 36,
    max_chars: int = 800,
) -> str:
    """Короткий сниппет живой темы для шага 5."""
    if not pool or not club_group_id:
        return ""
    topic_filter = ""
    args: list = [club_group_id, lookback_hours]
    n = 3
    if exclude_topic_id > 0:
        topic_filter = (
            f" AND COALESCE((m.metadata->>'message_thread_id')::bigint, 0) <> ${n}"
        )
        args.append(exclude_topic_id)
        n += 1
    args.append(8)
    sql = f"""
        SELECT m.content, u.first_name
        FROM messages m
        LEFT JOIN users u ON u.user_id = m.user_id
        WHERE m.chat_id = $1
          AND m.role = 'user'
          AND m.deleted_at IS NULL
          AND COALESCE(TRIM(m.content), '') <> ''
          AND m.created_at > NOW() - make_interval(hours => $2)
          {topic_filter}
        ORDER BY m.created_at DESC
        LIMIT ${n}
    """
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
    except Exception as e:
        logger.warning("fetch_recent_group_topic_snippet: %s", e)
        return ""
    parts = []
    for r in reversed(rows):
        name = (r.get("first_name") or "участник").strip() or "участник"
        text = str(r.get("content") or "").strip().replace("\n", " ")
        if len(text) > 160:
            text = text[:157] + "…"
        parts.append(f"{name}: {text}")
    blob = "\n".join(parts)
    return blob[:max_chars]
