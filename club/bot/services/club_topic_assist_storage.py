"""Хранение дедупа ответов topic-assist."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)


async def fetch_topic_messages(
    pool,
    *,
    chat_id: int,
    thread_id: int,
    since,
    until,
    limit: int = 400,
) -> List[Dict[str, Any]]:
    """Сообщения топика из messages за [since, until)."""
    if not pool or not chat_id or not thread_id:
        return []
    sql = """
        SELECT
            m.user_id,
            u.username,
            u.first_name,
            m.content,
            m.telegram_message_id,
            m.sender_type,
            m.created_at
        FROM messages m
        LEFT JOIN users u ON u.user_id = m.user_id
        WHERE m.chat_id = $1
          AND COALESCE((m.metadata->>'message_thread_id')::bigint, 0) = $2
          AND m.deleted_at IS NULL
          AND COALESCE(TRIM(m.content), '') <> ''
          AND m.created_at >= $3
          AND m.created_at < $4
          AND m.telegram_message_id IS NOT NULL
        ORDER BY m.created_at ASC
        LIMIT $5
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, chat_id, thread_id, since, until, limit)
    return [dict(r) for r in rows]


async def fetch_answered_source_ids(
    pool,
    *,
    chat_id: int,
    source_ids: Sequence[int],
) -> Set[int]:
    if not pool or not source_ids:
        return set()
    sql = """
        SELECT source_telegram_message_id
        FROM club_topic_assist_replies
        WHERE chat_id = $1
          AND source_telegram_message_id = ANY($2::bigint[])
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, chat_id, list(source_ids))
    return {int(r["source_telegram_message_id"]) for r in rows}


async def fetch_recent_bot_replies(
    pool,
    *,
    chat_id: int,
    thread_id: int,
    since,
    limit: int = 40,
) -> List[Dict[str, Any]]:
    if not pool:
        return []
    sql = """
        SELECT
            source_telegram_message_id,
            user_id,
            visibility,
            question_excerpt,
            answer_text,
            created_at
        FROM club_topic_assist_replies
        WHERE chat_id = $1
          AND thread_id = $2
          AND created_at >= $3
        ORDER BY created_at ASC
        LIMIT $4
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, chat_id, thread_id, since, limit)
    return [dict(r) for r in rows]


async def insert_reply(
    pool,
    *,
    chat_id: int,
    thread_id: int,
    source_telegram_message_id: int,
    user_id: int,
    visibility: str,
    question_excerpt: str,
    answer_text: str,
    bot_telegram_message_id: Optional[int] = None,
    ephemeral_message_id: Optional[int] = None,
    classify_reason: str = "",
) -> bool:
    """True если вставлено; False если уже был ответ (unique conflict)."""
    if not pool:
        return False
    sql = """
        INSERT INTO club_topic_assist_replies (
            chat_id, thread_id, source_telegram_message_id, user_id,
            visibility, question_excerpt, answer_text,
            bot_telegram_message_id, ephemeral_message_id, classify_reason
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        ON CONFLICT (chat_id, source_telegram_message_id) DO NOTHING
        RETURNING id
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchval(
                sql,
                chat_id,
                thread_id,
                source_telegram_message_id,
                user_id,
                visibility,
                (question_excerpt or "")[:500],
                (answer_text or "")[:4000],
                bot_telegram_message_id,
                ephemeral_message_id,
                (classify_reason or "")[:400],
            )
            return row is not None
    except Exception as e:
        logger.error("insert_reply failed: %s", e)
        return False


async def delete_reply_reservation(
    pool,
    *,
    chat_id: int,
    source_telegram_message_id: int,
) -> None:
    """Снять резерв дедупа, если отправка в Telegram не удалась."""
    if not pool:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                DELETE FROM club_topic_assist_replies
                WHERE chat_id = $1
                  AND source_telegram_message_id = $2
                  AND bot_telegram_message_id IS NULL
                  AND ephemeral_message_id IS NULL
                """,
                chat_id,
                source_telegram_message_id,
            )
    except Exception as e:
        logger.warning("delete_reply_reservation: %s", e)
