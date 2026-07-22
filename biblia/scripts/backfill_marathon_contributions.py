#!/usr/bin/env python3
"""
Ретроспективный учёт донатов в марафоне (все succeeded, order_id IS NULL за период).

Примеры:
  ./scripts/backfill_marathon_contributions.py --marathon-id 1 --dry-run
  ./scripts/backfill_marathon_contributions.py --marathon-id 1
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from bot.payments.currency_converter import CurrencyConverterService
from bot.services.donation_marathon_attr import backfill_marathon_contributions
from config import load_biblia_bot_config
from storage.user_storage import UserStorage

logger = logging.getLogger(__name__)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill marathon contributions from payments")
    parser.add_argument("--marathon-id", type=int, default=1, help="ID марафона (default: 1)")
    parser.add_argument("--dry-run", action="store_true", help="Только подсчёт, без записи")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg = load_biblia_bot_config()
    storage = UserStorage(cfg.database_url)
    await storage.connect()

    try:
        converter = CurrencyConverterService()
        stats = await backfill_marathon_contributions(
            storage,
            args.marathon_id,
            currency_converter=converter,
            dry_run=args.dry_run,
        )
        print("--- backfill marathon ---")
        for k, v in stats.items():
            print(f"{k}: {v}")
        return 0 if int(stats.get("errors") or 0) == 0 else 1
    finally:
        await storage.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
