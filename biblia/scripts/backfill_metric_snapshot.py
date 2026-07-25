#!/usr/bin/env python3
"""Создать metric_daily_snapshots и записать снапшот за вчера (без sudo)."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.services.biblia_daily_report import BibliaDailyReportCollector
from bot.services.metrics_snapshot_storage import MetricsSnapshotStorage
from config import load_biblia_bot_config
from storage.user_storage import UserStorage

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


async def main() -> int:
    cfg = load_biblia_bot_config()
    storage = UserStorage(cfg.database_url)
    await storage.connect()
    assert storage.pool
    collector = BibliaDailyReportCollector(storage.pool)
    metrics = await collector.get_all_metrics(save_snapshot=True)
    print(
        "ok snapshot",
        "dau=",
        metrics.get("dau"),
        "donation_buttons_shown=",
        metrics.get("donation_buttons_shown"),
        "donation_button_clicks=",
        metrics.get("donation_button_clicks"),
    )
    day = datetime.now(ZoneInfo("Europe/Moscow")).date() - timedelta(days=1)
    snap = await MetricsSnapshotStorage(storage.pool).get_snapshot(day)
    print(
        "read_back",
        day,
        bool(snap),
        snap.get("donation_buttons_shown") if snap else None,
    )
    await storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
