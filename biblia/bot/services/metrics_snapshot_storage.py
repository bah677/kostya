"""Снапшоты накопительных метрик для ежедневного отчёта Biblia."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_MSK = ZoneInfo("Europe/Moscow")
_BOT_NAME = "biblia"


class MetricsSnapshotStorage:
    def __init__(self, pool) -> None:
        self._pool = pool

    async def save_snapshot(self, metrics: Dict[str, Any]) -> bool:
        yesterday = (datetime.now(_MSK).date() - timedelta(days=1))
        now = datetime.now(_MSK)
        query = """
        INSERT INTO metric_snapshots (
            bot_name, snapshot_date, created_at,
            subscribers, dau, mau, messages, avg_messages_per_user,
            new_users, new_users_30d,
            new_referrals, new_referrals_30d,
            donations_amount, donations_month_to_date,
            donation_proposals, donation_buttons_shown, donation_button_clicks,
            donations_count, unique_donors,
            mailing_sent, mailing_success, mailing_failed
        ) VALUES (
            $1, $2, $3,
            $4, $5, $6, $7, $8,
            $9, $10,
            $11, $12,
            $13, $14,
            $15, $16, $17,
            $18, $19,
            $20, $21, $22
        )
        ON CONFLICT (bot_name, snapshot_date)
        DO NOTHING
        """
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    query,
                    _BOT_NAME,
                    yesterday,
                    now,
                    metrics.get("subscribers", 0),
                    metrics.get("dau", 0),
                    metrics.get("mau", 0),
                    metrics.get("messages", 0),
                    metrics.get("avg_messages_per_user", 0),
                    metrics.get("new_users_yesterday", 0),
                    metrics.get("new_users_30d", 0),
                    metrics.get("new_referrals_yesterday", 0),
                    metrics.get("new_referrals_30d", 0),
                    metrics.get("donations_yesterday", 0),
                    metrics.get("donations_month_to_date", 0),
                    metrics.get("donation_proposals", 0),
                    metrics.get("donation_buttons_shown", 0),
                    metrics.get("donation_button_clicks", 0),
                    metrics.get("donations_count", 0),
                    metrics.get("unique_donors", 0),
                    metrics.get("mailing_sent", 0),
                    metrics.get("mailing_success", 0),
                    metrics.get("mailing_failed", 0),
                )
            logger.info("💾 Снапшот метрик за %s сохранён", yesterday)
            return True
        except Exception as e:
            logger.error("❌ Ошибка сохранения снапшота: %s", e)
            return False

    async def get_snapshot(self, snapshot_date: date) -> Optional[Dict[str, Any]]:
        query = """
        SELECT * FROM metric_snapshots
        WHERE bot_name = $1 AND snapshot_date = $2
        """
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(query, _BOT_NAME, snapshot_date)
                return dict(row) if row else None
        except Exception as e:
            logger.error("❌ Ошибка чтения снапшота: %s", e)
            return None
