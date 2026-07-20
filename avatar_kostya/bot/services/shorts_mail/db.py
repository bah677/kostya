"""Запись mailing_campaigns / mailing_audience в club или biblia."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

import asyncpg

logger = logging.getLogger(__name__)


async def create_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, min_size=1, max_size=3)


async def create_mailing_campaign(
    pool: asyncpg.Pool,
    *,
    name: str,
    text: str,
    scheduled_at: datetime,
    created_by: int,
    buttons: List[Dict[str, Any]],
    attachments: List[Dict[str, str]],
    parse_mode: str = "HTML",
    has_ref_link: bool = False,
) -> Optional[int]:
    media_type = attachments[0]["type"] if len(attachments) == 1 else None
    media_file_id = attachments[0]["file_id"] if len(attachments) == 1 else None
    async with pool.acquire() as conn:
        cid = await conn.fetchval(
            """
            INSERT INTO mailing_campaigns (
                name, text, parse_mode, scheduled_at, status, has_ref_link,
                media_type, media_file_id, created_by, buttons, attachments
            ) VALUES (
                $1, $2, $3, $4, 'planned', $5,
                $6, $7, $8, $9::jsonb, $10::jsonb
            )
            RETURNING id
            """,
            name,
            text,
            parse_mode,
            scheduled_at,
            has_ref_link,
            media_type,
            media_file_id,
            created_by,
            json.dumps(buttons, ensure_ascii=False),
            json.dumps(attachments, ensure_ascii=False),
        )
        return int(cid) if cid is not None else None


async def add_audience(pool: asyncpg.Pool, campaign_id: int, user_ids: Sequence[int]) -> int:
    added = 0
    async with pool.acquire() as conn:
        for uid in user_ids:
            result = await conn.execute(
                """
                INSERT INTO mailing_audience (campaign_id, user_id)
                VALUES ($1, $2)
                ON CONFLICT (campaign_id, user_id) DO NOTHING
                """,
                campaign_id,
                int(uid),
            )
            if result and "INSERT 0 1" in result:
                added += 1
    return added


async def fetch_club_audience(pool: asyncpg.Pool, segment: str) -> List[int]:
    if segment == "all":
        q = "SELECT user_id FROM users WHERE is_active = TRUE"
    elif segment == "has_license":
        q = """
            SELECT u.user_id FROM users u WHERE u.is_active = TRUE
            AND EXISTS (
              SELECT 1 FROM license l
              WHERE l.user_id = u.user_id AND l.expires_at > NOW()
            )
        """
    elif segment == "no_license":
        q = """
            SELECT u.user_id FROM users u WHERE u.is_active = TRUE
            AND NOT EXISTS (
              SELECT 1 FROM license l
              WHERE l.user_id = u.user_id AND l.expires_at > NOW()
            )
        """
    else:
        raise ValueError(segment)
    async with pool.acquire() as conn:
        rows = await conn.fetch(q)
    return [int(r["user_id"]) for r in rows]


async def fetch_biblia_active(pool: asyncpg.Pool) -> List[int]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id FROM users WHERE is_active = TRUE ORDER BY user_id ASC"
        )
    return [int(r["user_id"]) for r in rows]


async def fetch_biblia_donors(pool: asyncpg.Pool, *, min_donations: int) -> List[int]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT p.user_id
            FROM payments p
            JOIN users u ON u.user_id = p.user_id
            WHERE p.status = 'succeeded'
              AND u.is_active = TRUE
            GROUP BY p.user_id
            HAVING COUNT(*) >= $1
            ORDER BY p.user_id
            """,
            min_donations,
        )
    return [int(r["user_id"]) for r in rows]


async def fetch_biblia_challenge_uids(pool: asyncpg.Pool) -> List[int]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT sc.user_id
              FROM scripture_challenges sc
              JOIN users u ON u.user_id = sc.user_id
             WHERE sc.status IN ('signup', 'planning', 'active')
               AND u.is_active = TRUE
             ORDER BY sc.user_id ASC
            """
        )
    return [int(r["user_id"]) for r in rows]


async def list_recent_campaigns(pool: asyncpg.Pool, limit: int = 30) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, created_at, status
            FROM mailing_campaigns
            WHERE name NOT ILIKE '%тест%'
              AND name NOT ILIKE '%(авто)%'
            ORDER BY id DESC
            LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]


async def audience_uids_for_campaigns(pool: asyncpg.Pool, campaign_ids: List[int]) -> List[int]:
    if not campaign_ids:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT user_id
            FROM mailing_audience
            WHERE campaign_id = ANY($1::bigint[])
            """,
            campaign_ids,
        )
    return [int(r["user_id"]) for r in rows]
