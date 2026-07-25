"""Снапшоты накопительных метрик для ежедневного отчёта Biblia."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_MSK = ZoneInfo("Europe/Moscow")
_BOT_NAME = "biblia"

# Своя таблица: bot_user — владелец. Старая metric_snapshots часто создана от postgres
# без GRANT на biblia_bot_user → permission denied.
_TABLE = "metric_daily_snapshots"


_ENSURE_SQL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
  id BIGSERIAL PRIMARY KEY,
  bot_name TEXT NOT NULL DEFAULT 'biblia',
  snapshot_date DATE NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  subscribers INTEGER NOT NULL DEFAULT 0,
  dau INTEGER NOT NULL DEFAULT 0,
  mau INTEGER NOT NULL DEFAULT 0,
  messages INTEGER NOT NULL DEFAULT 0,
  avg_messages_per_user DOUBLE PRECISION NOT NULL DEFAULT 0,
  new_users INTEGER NOT NULL DEFAULT 0,
  new_users_30d INTEGER NOT NULL DEFAULT 0,
  new_referrals INTEGER NOT NULL DEFAULT 0,
  new_referrals_30d INTEGER NOT NULL DEFAULT 0,
  donations_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
  donations_month_to_date DOUBLE PRECISION NOT NULL DEFAULT 0,
  donation_proposals INTEGER NOT NULL DEFAULT 0,
  donation_buttons_shown INTEGER NOT NULL DEFAULT 0,
  donation_button_clicks INTEGER NOT NULL DEFAULT 0,
  donations_count INTEGER NOT NULL DEFAULT 0,
  unique_donors INTEGER NOT NULL DEFAULT 0,
  mailing_sent INTEGER NOT NULL DEFAULT 0,
  mailing_success INTEGER NOT NULL DEFAULT 0,
  mailing_failed INTEGER NOT NULL DEFAULT 0,
  UNIQUE (bot_name, snapshot_date)
);
"""

_GRANT_VIEWERS_SQL = f"""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgresqlmironviewer') THEN
    EXECUTE 'GRANT SELECT ON TABLE {_TABLE} TO postgresqlmironviewer';
    EXECUTE 'GRANT SELECT ON SEQUENCE {_TABLE}_id_seq TO postgresqlmironviewer';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agency_ro') THEN
    EXECUTE 'GRANT SELECT ON TABLE {_TABLE} TO agency_ro';
    EXECUTE 'GRANT SELECT ON SEQUENCE {_TABLE}_id_seq TO agency_ro';
  END IF;
END$$;
"""


class MetricsSnapshotStorage:
    def __init__(self, pool) -> None:
        self._pool = pool
        self._ensured = False

    async def ensure_schema(self) -> None:
        if self._ensured:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(_ENSURE_SQL)
            try:
                await conn.execute(_GRANT_VIEWERS_SQL)
            except Exception as e:
                logger.warning("grant SELECT on %s to viewers: %s", _TABLE, e)
        self._ensured = True
        logger.info("metric snapshots table ready: %s", _TABLE)

    async def save_snapshot(
        self, metrics: Dict[str, Any], *, snapshot_date: Optional[date] = None
    ) -> bool:
        await self.ensure_schema()
        day = snapshot_date or (datetime.now(_MSK).date() - timedelta(days=1))
        now = datetime.now(_MSK)
        query = f"""
        INSERT INTO {_TABLE} (
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
        DO UPDATE SET
            created_at = EXCLUDED.created_at,
            subscribers = EXCLUDED.subscribers,
            dau = EXCLUDED.dau,
            mau = EXCLUDED.mau,
            messages = EXCLUDED.messages,
            avg_messages_per_user = EXCLUDED.avg_messages_per_user,
            new_users = EXCLUDED.new_users,
            new_users_30d = EXCLUDED.new_users_30d,
            new_referrals = EXCLUDED.new_referrals,
            new_referrals_30d = EXCLUDED.new_referrals_30d,
            donations_amount = EXCLUDED.donations_amount,
            donations_month_to_date = EXCLUDED.donations_month_to_date,
            donation_proposals = EXCLUDED.donation_proposals,
            donation_buttons_shown = EXCLUDED.donation_buttons_shown,
            donation_button_clicks = EXCLUDED.donation_button_clicks,
            donations_count = EXCLUDED.donations_count,
            unique_donors = EXCLUDED.unique_donors,
            mailing_sent = EXCLUDED.mailing_sent,
            mailing_success = EXCLUDED.mailing_success,
            mailing_failed = EXCLUDED.mailing_failed
        """
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    query,
                    _BOT_NAME,
                    day,
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
            logger.info("💾 Снапшот метрик за %s сохранён (%s)", day, _TABLE)
            return True
        except Exception as e:
            logger.error("❌ Ошибка сохранения снапшота: %s", e)
            return False

    async def get_snapshot(self, snapshot_date: date) -> Optional[Dict[str, Any]]:
        await self.ensure_schema()
        # сначала новая таблица; fallback на старую metric_snapshots если вдруг есть права
        for table in (_TABLE, "metric_snapshots"):
            query = f"""
            SELECT * FROM {table}
            WHERE bot_name = $1 AND snapshot_date = $2
            ORDER BY id DESC
            LIMIT 1
            """
            try:
                async with self._pool.acquire() as conn:
                    row = await conn.fetchrow(query, _BOT_NAME, snapshot_date)
                    if row:
                        return dict(row)
            except Exception as e:
                logger.debug("get_snapshot %s: %s", table, e)
        return None
