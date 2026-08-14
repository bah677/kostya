#!/usr/bin/env python3
"""Разовый reconcile pending-платежей с агрегаторами (после сбоя PaymentChecker).

Пример (прод-БД, платежи с 13.08.2026):

  cd /home/appuser/dev/kostya/club
  ./venv/bin/python scripts/reconcile_pending_payments.py \\
    --env-file /home/appuser/club/.env \\
    --since 2026-08-13 --apply
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("reconcile_pending_payments")


def _load_env(env_file: str | None) -> None:
    if env_file:
        load_dotenv(env_file, override=True)
    else:
        load_dotenv(override=True)


def _parse_since(raw: str) -> datetime:
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"bad --since: {raw!r}")


async def run(*, env_file: str | None, since: datetime, apply: bool, max_age_days: int) -> None:
    _load_env(env_file)
    # config читает os.environ при импорте
    from config import config
    from bot.core import TelegramBot

    logger.info(
        "DB=%s since=%s apply=%s max_age_days=%s",
        config.DB_NAME,
        since.isoformat(),
        apply,
        max_age_days,
    )

    app = TelegramBot()
    await app.initialize()
    try:
        checker = app.payment_checker
        if checker is None:
            raise RuntimeError("PaymentChecker не инициализирован")

        # останавливаем фоновый цикл — работаем только разовым проходом
        await checker.stop()

        pending = await app.user_storage.get_pending_payments_for_poll(
            max_age_days=max_age_days
        )
        since_n = since.replace(tzinfo=None)
        filtered = []
        for p in pending:
            created = p.get("created_at")
            if created is None:
                filtered.append(p)
                continue
            c = created.replace(tzinfo=None) if getattr(created, "tzinfo", None) else created
            if c >= since_n:
                filtered.append(p)

        logger.info("К проверке: %s pending (из %s за %sd)", len(filtered), len(pending), max_age_days)
        for p in filtered:
            logger.info(
                "  id=%s user=%s %s %s %s order=%s pid=%s",
                p.get("id"),
                p.get("user_id"),
                p.get("payment_provider"),
                p.get("amount"),
                p.get("currency"),
                p.get("order_id"),
                (p.get("provider_payment_id") or "")[:36],
            )

        if not apply:
            logger.info("dry-run: агрегаторы не опрашивались, статусы не менялись")
            return

        stats = await checker.force_check_pending(
            since=since,
            max_age_days=max_age_days,
            ignore_schedule=True,
        )
        logger.info("Результат reconcile: %s", stats)
    finally:
        await app.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--since", type=_parse_since, required=True)
    parser.add_argument("--max-age-days", type=int, default=30)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Реально опросить провайдеров и финализировать (без флага — dry-run)",
    )
    args = parser.parse_args()
    asyncio.run(
        run(
            env_file=args.env_file,
            since=args.since,
            apply=args.apply,
            max_age_days=args.max_age_days,
        )
    )


if __name__ == "__main__":
    main()
