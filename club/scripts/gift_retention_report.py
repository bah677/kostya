#!/usr/bin/env python3
"""
Результат тестовых когорт удержания подарочной волны.

Считает по каждому варианту: размер когорты, сколько писем ушло, сколько
человек заплатило и на какую сумму. Контроль — строка для сравнения.

  cd /home/appuser/dev/kostya/club
  python3 scripts/gift_retention_report.py --env-file /home/appuser/club/.env

Читает только SELECT, ничего не меняет. Можно гонять хоть каждый день.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import load_config
from storage.db.gift_retention_experiment import (
    CAMPAIGN_GIFT_2026_09,
    VARIANT_CONTROL,
)
from storage.user_storage import UserStorage

# Оплатой считаем только succeeded и только после попадания в когорту:
# привязки карты на 15 ₽ и отменённые попытки в зачёт не идут.
_SQL = """
WITH e AS (
    SELECT user_id, variant, assigned_at
    FROM gift_retention_experiment
    WHERE campaign = $1
),
sent AS (
    SELECT e.variant, s.outreach_slug, count(DISTINCT s.user_id) AS n
    FROM e JOIN subscription_outreach_sent s ON s.user_id = e.user_id
    WHERE s.outreach_slug LIKE 'expiry_minus_%'
    GROUP BY 1, 2
),
paid AS (
    SELECT e.variant,
           count(DISTINCT p.user_id) AS payers,
           coalesce(sum(p.amount), 0) AS revenue
    FROM e JOIN payments p ON p.user_id = e.user_id
    WHERE p.status = 'succeeded'
      AND p.created_at >= e.assigned_at
      AND p.amount >= 100
    GROUP BY 1
)
SELECT e.variant,
       count(*) AS cohort,
       coalesce(max(pd.payers), 0) AS payers,
       coalesce(max(pd.revenue), 0) AS revenue,
       coalesce(max(s10.n), 0) AS sent_10d,
       coalesce(max(s5.n), 0)  AS sent_5d,
       coalesce(max(s1.n), 0)  AS sent_1d
FROM e
LEFT JOIN paid pd ON pd.variant = e.variant
LEFT JOIN sent s10 ON s10.variant = e.variant AND s10.outreach_slug = 'expiry_minus_10d_ord1'
LEFT JOIN sent s5  ON s5.variant  = e.variant AND s5.outreach_slug  = 'expiry_minus_5d_ord1'
LEFT JOIN sent s1  ON s1.variant  = e.variant AND s1.outreach_slug  = 'expiry_minus_1d_ord1'
GROUP BY e.variant
ORDER BY (e.variant = 'control'), e.variant
"""

_TITLES = {
    "p1_discount": "скидка 30% (840 ₽)",
    "p2_sell5d": "письмо −5д продаёт",
    "p3_group_close": "честно про группу",
    "p4_step299": "две недели 299 ₽",
    VARIANT_CONTROL: "контроль (как было)",
}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default="")
    ap.add_argument("--campaign", default=CAMPAIGN_GIFT_2026_09)
    args = ap.parse_args()

    if args.env_file:
        from dotenv import load_dotenv

        load_dotenv(args.env_file, override=True)
    cfg = load_config()
    storage = UserStorage(cfg.database_url)
    await storage.initialize()
    try:
        async with storage.get_connection() as conn:
            rows = await conn.fetch(_SQL, args.campaign)
        if not rows:
            print("Когорты не размечены — сначала assign_gift_retention_cohorts.py --apply")
            return 1
        print(f"\nКампания {args.campaign}\n")
        head = (
            f"{'вариант':<17}{'что проверяем':<24}{'чел':>4}"
            f"{'−10д':>6}{'−5д':>6}{'−1д':>6}{'оплат':>7}{'конв.':>8}{'сумма':>10}"
        )
        print(head)
        print("-" * len(head))
        for r in rows:
            conv = (100.0 * r["payers"] / r["cohort"]) if r["cohort"] else 0.0
            print(
                f"{r['variant']:<17}{_TITLES.get(r['variant'], ''):<24}"
                f"{r['cohort']:>4}{r['sent_10d']:>6}{r['sent_5d']:>6}{r['sent_1d']:>6}"
                f"{r['payers']:>7}{conv:>7.1f}%{int(r['revenue']):>10}"
            )
        print(
            "\nКогорты по 10 человек: одна оплата — это 10 процентных пунктов. "
            "Цифры показывают направление, а не статистически значимую разницу."
        )
        return 0
    finally:
        await storage.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
