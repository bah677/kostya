#!/usr/bin/env python3
"""
Бэкфилл amount_rub и exchange_rate для успешных донатов (order_id IS NULL).

- RUB: amount_rub = amount, exchange_rate = 1.0
- USD/EUR/…: курс ЦБ на дату операции (completed_at → updated_at → created_at)

Примеры:
  ./scripts/backfill_payment_amount_rub.py --dry-run
  ./scripts/backfill_payment_amount_rub.py
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from bot.payments.currency_converter import CurrencyConverterService
from bot.payments.payment_conversion import compute_payment_rub_from_row
from config import load_biblia_bot_config
from storage.user_storage import UserStorage

logger = logging.getLogger(__name__)

_BACKFILL_SQL = """
SELECT *
FROM payments
WHERE status = 'succeeded'
  AND order_id IS NULL
  AND (
    amount_rub IS NULL
    OR exchange_rate IS NULL
    OR exchange_rate = 0
  )
ORDER BY created_at ASC
"""


async def _fetch_payments(storage: UserStorage) -> List[Dict[str, Any]]:
    async with storage.get_connection() as conn:
        rows = await conn.fetch(_BACKFILL_SQL)
    return [dict(row) for row in rows]


def _resolve_conversion(
    payment: Dict[str, Any],
    fx_rub: Optional[float],
    fx_rate: Optional[float],
) -> Tuple[Optional[float], Optional[float]]:
    currency = (payment.get("currency") or "RUB").strip().upper()
    amount = float(payment.get("amount") or 0)
    existing_rub = payment.get("amount_rub")

    if currency == "RUB":
        rub = float(existing_rub) if existing_rub is not None else amount
        return rub, 1.0

    if existing_rub is not None and amount:
        return float(existing_rub), float(existing_rub) / amount

    return fx_rub, fx_rate


async def run_backfill(*, dry_run: bool) -> int:
    config = load_biblia_bot_config()
    storage = UserStorage(config.database_url)
    await storage.initialize()
    converter = CurrencyConverterService()

    try:
        payments = await _fetch_payments(storage)
        if not payments:
            print("Нет донатов для бэкфилла (все amount_rub/exchange_rate заполнены).")
            return 0

        rub_cnt = sum(
            1
            for p in payments
            if (p.get("currency") or "RUB").strip().upper() == "RUB"
        )
        fx_cnt = len(payments) - rub_cnt
        print(
            f"Найдено донатов для бэкфилла: {len(payments)} "
            f"(RUB: {rub_cnt}, валютные: {fx_cnt})"
        )

        updated = 0
        failed = 0

        for payment in payments:
            pid = int(payment["id"])
            currency = (payment.get("currency") or "RUB").strip().upper()
            amount = float(payment.get("amount") or 0)

            fx_rub, fx_rate = await compute_payment_rub_from_row(converter, payment)
            rub_amount, exchange_rate = _resolve_conversion(payment, fx_rub, fx_rate)

            if rub_amount is None or exchange_rate is None:
                failed += 1
                print(
                    f"  SKIP id={pid} {amount} {currency} — не удалось рассчитать конвертацию",
                    file=sys.stderr,
                )
                continue

            print(
                f"  id={pid} {amount} {currency} → {rub_amount:.2f} RUB "
                f"(rate={exchange_rate:.6g})"
            )
            if dry_run:
                updated += 1
                continue

            ok = await storage.update_payment_with_conversion(
                pid,
                rub_amount,
                exchange_rate,
            )
            if ok:
                updated += 1
            else:
                failed += 1
                print(f"  FAIL id={pid} — UPDATE не прошёл", file=sys.stderr)

        mode = "DRY-RUN" if dry_run else "APPLIED"
        print(
            f"\n{mode}: обновлено {updated}, пропущено/ошибок {failed}, всего {len(payments)}"
        )
        return 1 if failed else 0
    finally:
        await storage.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Бэкфилл amount_rub/exchange_rate для донатов (standalone payments)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="только показать пересчёт, без записи в БД",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    exit_code = asyncio.run(run_backfill(dry_run=args.dry_run))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
