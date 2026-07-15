#!/usr/bin/env python3
"""Одноразовый backfill attribution_touches из interaction_logs."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.services.attribution_touch import parse_callback_data, parse_start_text
from bot.services.report_exclude import report_exclude_user_ids
from config import config
from storage.user_storage import UserStorage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backfill_attribution")


async def run(*, dry_run: bool, batch: int, since: str | None) -> None:
    storage = UserStorage(config.database_url)
    await storage.initialize()
    exclude = set(report_exclude_user_ids())
    since_clause = ""
    args: list = []
    if since:
        since_clause = " AND il.created_at >= $1::timestamptz"
        args.append(since)

    q = f"""
        SELECT il.user_id, il.event_category, il.data, il.callback_data, il.created_at
        FROM interaction_logs il
        WHERE il.user_id IS NOT NULL
          {since_clause}
          AND (
            (il.event_category = 'message' AND COALESCE(il.data->>'text', '') ILIKE '/start%')
            OR (il.event_category = 'callback' AND il.callback_data IS NOT NULL)
          )
        ORDER BY il.created_at ASC
    """
    inserted = 0
    skipped = 0
    async with storage.get_connection() as conn:
        rows = await conn.fetch(q, *args)
        logger.info("Строк в логах для разбора: %s", len(rows))
        for row in rows:
            uid = int(row["user_id"])
            if uid in exclude:
                skipped += 1
                continue
            parsed = None
            if row["event_category"] == "message":
                import json

                raw_data = row["data"]
                if isinstance(raw_data, str):
                    try:
                        raw_data = json.loads(raw_data)
                    except Exception:
                        raw_data = {}
                elif raw_data is None:
                    raw_data = {}
                text = (raw_data.get("text") if isinstance(raw_data, dict) else "") or ""
                parsed = parse_start_text(text)
                source_type = "start"
            else:
                parsed = parse_callback_data(row["callback_data"] or "")
                source_type = "callback"
            if not parsed:
                skipped += 1
                continue
            if dry_run:
                inserted += 1
                continue
            ok = await storage.record_attribution_touch(
                uid,
                parsed,
                source_type=source_type,
                created_at=row["created_at"],
            )
            if ok:
                inserted += 1
        if not dry_run:
            await conn.execute(
                """
                UPDATE orders o SET
                    pay_last_touch_key = t.touch_key,
                    pay_last_touch_at = t.created_at
                FROM (
                    SELECT DISTINCT ON (o2.id)
                        o2.id AS order_id,
                        at.touch_key,
                        at.created_at
                    FROM orders o2
                    JOIN payments p ON p.order_id = o2.id AND p.status = 'succeeded'
                    JOIN attribution_touches at ON at.user_id = o2.user_id
                        AND at.created_at <= COALESCE(o2.paid_at, p.completed_at)
                    WHERE o2.status = 'paid'
                      AND at.touch_key NOT LIKE 'payment_select_%'
                      AND at.touch_key NOT LIKE 'payment_currency_rub_%'
                      AND at.touch_key NOT LIKE 'payment_currency_usd_%'
                    ORDER BY o2.id, at.created_at DESC
                ) t
                WHERE o.id = t.order_id AND o.pay_last_touch_key IS NULL
                """
            )
    logger.info("Готово: touches=%s skipped=%s dry_run=%s", inserted, skipped, dry_run)
    if hasattr(storage, "close"):
        await storage.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--since", default=None, help="YYYY-MM-DD")
    p.add_argument("--batch", type=int, default=5000)
    args = p.parse_args()
    asyncio.run(run(dry_run=args.dry_run, batch=args.batch, since=args.since))


if __name__ == "__main__":
    main()
