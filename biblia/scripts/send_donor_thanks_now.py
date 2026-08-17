#!/usr/bin/env python3
"""Разовая / тестовая отправка утренней благодарности донорам."""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.services.donor_thanks_morning import (  # noqa: E402
    build_donor_thanks_html,
    run_donor_thanks_morning,
)
from config import load_biblia_bot_config  # noqa: E402
from storage.user_storage import UserStorage  # noqa: E402


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--env", required=True)
    p.add_argument("--quota-day", required=True, help="YYYY-MM-DD")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


async def main() -> None:
    args = _parse()
    load_dotenv(args.env, override=True)
    cfg = load_biblia_bot_config()
    day = date.fromisoformat(args.quota_day)

    storage = UserStorage(cfg.database_url)
    await storage.initialize()
    bot = Bot(
        token=cfg.BIBLIA_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        if args.dry_run:
            from bot.payments.currency_converter import CurrencyConverterService
            from bot.services.donor_thanks_morning import (
                list_admin_recipient_ids,
                list_succeeded_donor_user_ids,
                pick_thanks_quote,
            )
            from bot.services.prayer_voice_funding import PrayerVoiceFundingService
            from bot.services.prayer_voice_quota import previous_quota_day, quota_window

            funding = PrayerVoiceFundingService(storage, CurrencyConverterService())
            period = await funding.ensure_period(day)
            prev = previous_quota_day(day)
            start, end = quota_window(prev)
            donors = await list_succeeded_donor_user_ids(storage, start, end)
            admins = await list_admin_recipient_ids(storage)
            text = build_donor_thanks_html(
                limit_slots=period.limit_slots,
                quote_html=pick_thanks_quote(on_day=day),
            )
            print(text)
            print("---")
            print("quota_day", day, "limit", period.limit_slots)
            print("window", start.isoformat(), "→", end.isoformat())
            print("donors", len(donors), donors)
            print("admins", len(admins), admins)
            return

        result = await run_donor_thanks_morning(
            bot,
            storage,
            quota_day=day,
            force=bool(args.force),
        )
        print(
            f"skipped={result.skipped} day={result.quota_day} "
            f"limit={result.limit_slots} donors={len(result.donor_ids)} "
            f"admins={len(result.admin_ids)} ok={result.sent_ok} fail={result.sent_fail}"
        )
        if result.donor_ids:
            print("donors:", ", ".join(str(x) for x in result.donor_ids))
    finally:
        await bot.session.close()
        await storage.close()


if __name__ == "__main__":
    asyncio.run(main())
