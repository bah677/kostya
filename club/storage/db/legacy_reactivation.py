"""Mixin: разовый вывод легаси status 103 (с диалогом) в stuck_dialog."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from bot.services.report_exclude import sql_exclude_users

logger = logging.getLogger(__name__)

_HAS_DIALOG_SQL = """
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
    )
"""

_REACTED_SQL = """
    (
        EXISTS (
            SELECT 1 FROM messages m
            WHERE m.user_id = r.user_id
              AND m.chat_type = 'private'
              AND m.role = 'user'
              AND m.deleted_at IS NULL
              AND m.created_at > r.migrated_at
              AND COALESCE(m.message_type, '') <> 'callback'
              AND m.content IS NOT NULL
              AND TRIM(m.content) <> ''
              AND m.content NOT ILIKE '/start%%'
              AND LENGTH(TRIM(m.content)) > 2
        )
        OR EXISTS (
            SELECT 1 FROM interaction_logs il
            WHERE il.user_id = r.user_id
              AND il.event_category = 'followup'
              AND il.event_type IN (
                  'followup_stuck_answer_delivered',
                  'followup_stuck_cta_sent'
              )
              AND il.created_at > r.migrated_at
        )
        OR EXISTS (
            SELECT 1 FROM orders o
            JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
            WHERE o.user_id = r.user_id
              AND COALESCE(p.completed_at, o.paid_at, p.updated_at) > r.migrated_at
        )
    )
"""


class LegacyReactivationMixin:
    async def fetch_legacy_103_dialog_candidates(
        self, *, limit: int
    ) -> List[Dict[str, Any]]:
        exclude_sql, exclude_ids = sql_exclude_users("u.user_id", start_param=2)
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT
                        u.user_id,
                        fs.segment,
                        fs.last_topic,
                        (
                            SELECT MAX(m.created_at)
                            FROM messages m
                            WHERE m.user_id = u.user_id
                              AND m.chat_type = 'private'
                              AND m.role = 'assistant'
                              AND m.deleted_at IS NULL
                        ) AS last_assistant_at
                    FROM users u
                    INNER JOIN followup_states fs ON fs.user_id = u.user_id
                    WHERE u.is_active IS TRUE
                      AND fs.status = 103
                      AND NOT EXISTS (
                          SELECT 1 FROM license l
                          WHERE l.user_id = u.user_id
                            AND l.status = 'active'
                            AND l.expires_at > NOW()
                      )
                      AND {_HAS_DIALOG_SQL}
                      AND NOT EXISTS (
                          SELECT 1 FROM legacy_103_reactivation lr
                          WHERE lr.user_id = u.user_id
                      )
                      {exclude_sql}
                    ORDER BY last_assistant_at DESC NULLS LAST
                    LIMIT $1
                    """,
                    limit,
                    *exclude_ids,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("fetch_legacy_103_dialog_candidates failed: %s", e)
            return []

    async def record_legacy_103_reactivation(
        self,
        user_id: int,
        *,
        ping_delivered: bool,
        skip_reason: Optional[str] = None,
    ) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO legacy_103_reactivation
                        (user_id, migrated_at, ping_delivered, skip_reason)
                    VALUES ($1, NOW(), $2, $3)
                    ON CONFLICT (user_id) DO UPDATE SET
                        migrated_at = EXCLUDED.migrated_at,
                        ping_delivered = EXCLUDED.ping_delivered,
                        skip_reason = EXCLUDED.skip_reason
                    """,
                    user_id,
                    ping_delivered,
                    skip_reason,
                )
        except Exception as e:
            logger.error(
                "record_legacy_103_reactivation user %s failed: %s", user_id, e
            )

    async def count_legacy_103_dialog_remaining(self) -> int:
        exclude_sql, exclude_ids = sql_exclude_users("u.user_id")
        try:
            async with self.get_connection() as conn:
                n = await conn.fetchval(
                    f"""
                    SELECT COUNT(*)::int
                    FROM users u
                    INNER JOIN followup_states fs ON fs.user_id = u.user_id
                    WHERE u.is_active IS TRUE
                      AND fs.status = 103
                      AND NOT EXISTS (
                          SELECT 1 FROM license l
                          WHERE l.user_id = u.user_id
                            AND l.status = 'active'
                            AND l.expires_at > NOW()
                      )
                      AND {_HAS_DIALOG_SQL}
                      AND NOT EXISTS (
                          SELECT 1 FROM legacy_103_reactivation lr
                          WHERE lr.user_id = u.user_id
                      )
                      {exclude_sql}
                    """,
                    *exclude_ids,
                )
                return int(n or 0)
        except Exception as e:
            logger.error("count_legacy_103_dialog_remaining failed: %s", e)
            return 0

    async def collect_legacy_103_reactivation_stats(
        self, *, report_day: Optional[date] = None
    ) -> Dict[str, Any]:
        """Сводка для ежедневного отчёта."""
        remaining = await self.count_legacy_103_dialog_remaining()
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    f"""
                    SELECT
                        COUNT(*)::int AS migrated_total,
                        COUNT(*) FILTER (
                            WHERE ping_delivered IS TRUE
                        )::int AS ping_sent_total,
                        COUNT(*) FILTER (
                            WHERE ping_delivered IS TRUE AND {_REACTED_SQL}
                        )::int AS reacted_total,
                        COUNT(*) FILTER (
                            WHERE migrated_at::date = $1::date
                        )::int AS migrated_yesterday,
                        COUNT(*) FILTER (
                            WHERE migrated_at::date = $1::date
                              AND ping_delivered IS TRUE
                        )::int AS ping_sent_yesterday,
                        COUNT(*) FILTER (
                            WHERE migrated_at::date = $1::date
                              AND ping_delivered IS TRUE
                              AND {_REACTED_SQL}
                        )::int AS reacted_yesterday
                    FROM legacy_103_reactivation r
                    """,
                    report_day,
                )
        except Exception as e:
            logger.error("collect_legacy_103_reactivation_stats failed: %s", e)
            return {
                "remaining": remaining,
                "migrated_total": 0,
                "ping_sent_total": 0,
                "reacted_total": 0,
                "migrated_yesterday": 0,
                "ping_sent_yesterday": 0,
                "reacted_yesterday": 0,
            }

        if not row:
            return {
                "remaining": remaining,
                "migrated_total": 0,
                "ping_sent_total": 0,
                "reacted_total": 0,
                "migrated_yesterday": 0,
                "ping_sent_yesterday": 0,
                "reacted_yesterday": 0,
            }

        return {
            "remaining": remaining,
            "migrated_total": int(row["migrated_total"] or 0),
            "ping_sent_total": int(row["ping_sent_total"] or 0),
            "reacted_total": int(row["reacted_total"] or 0),
            "migrated_yesterday": int(row["migrated_yesterday"] or 0),
            "ping_sent_yesterday": int(row["ping_sent_yesterday"] or 0),
            "reacted_yesterday": int(row["reacted_yesterday"] or 0),
        }
