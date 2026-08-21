#!/usr/bin/env python3
"""Разовый бэкфилл журнала ожидаемого прихода (35% донатов Biblia → USD)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.payments.currency_converter import CurrencyConverterService
from bot.services.ledger_expected_income import (
    JOURNAL_START,
    LedgerExpectedIncomeService,
    completed_day_for_report,
)
from config import config
from storage.user_storage import UserStorage


async def main() -> None:
    dsn = config.biblia_mail_database_url
    if not dsn:
        raise SystemExit("Нет biblia_mail_database_url в .env (BIBLIA_MAIL_DB_*)")
    storage = UserStorage(config.database_url)
    await storage.initialize()
    svc = LedgerExpectedIncomeService(
        storage,
        currency_converter=CurrencyConverterService(),
        biblia_dsn=dsn,
    )
    await svc.start()
    end = completed_day_for_report()
    n = await svc.sync_through(end, force_from=JOURNAL_START)
    row = await svc.get_for_report()
    print(f"start={JOURNAL_START} end={end} upserted={n}")
    if row:
        print(
            f"as_of={row['day']} cumulative_share_usd={row['cumulative_share_usd']} "
            f"day_share={row['day_share_usd']} rub={row['donations_rub']} "
            f"usd={row['donations_usd']} rate={row['usd_rub_rate']} n={row['donations_count']}"
        )
    await svc.close()
    await storage.close()


if __name__ == "__main__":
    asyncio.run(main())
