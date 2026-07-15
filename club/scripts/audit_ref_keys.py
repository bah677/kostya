#!/usr/bin/env python3
"""Аудит ref_keys: кампании из логов/касаний vs справочник ref_keys."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import config
from storage.user_storage import UserStorage

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("audit_ref_keys")

_CAMPAIGN_REF_RE = r"^[a-zA-Z0-9_]+$"


async def run(*, show_user_refs: bool) -> None:
    storage = UserStorage(config.database_url)
    await storage.initialize()

    async with storage.get_connection() as conn:
        in_ref_keys = await conn.fetch("SELECT ref_key, name, type FROM ref_keys ORDER BY ref_key")
        ref_set = {r["ref_key"] for r in in_ref_keys}

        user_refs = await conn.fetch(
            """
            SELECT first_touch_key AS k, COUNT(*) AS cnt
            FROM users
            WHERE first_touch_key LIKE 'ref_%'
            GROUP BY first_touch_key
            """
        )
        touch_refs = await conn.fetch(
            """
            SELECT touch_key AS k, COUNT(*) AS cnt
            FROM attribution_touches
            WHERE touch_key LIKE 'ref_%'
            GROUP BY touch_key
            """
        )
        log_refs = await conn.fetch(
            """
            SELECT substring(data->>'text' FROM 'ref_[a-zA-Z0-9_]+') AS k,
                   COUNT(*) AS cnt
            FROM interaction_logs
            WHERE COALESCE(data->>'text', '') LIKE '%ref_%'
            GROUP BY 1
            HAVING substring(data->>'text' FROM 'ref_[a-zA-Z0-9_]+') IS NOT NULL
            """
        )

    merged: dict[str, dict[str, int]] = {}
    for src, rows in (
        ("users", user_refs),
        ("touches", touch_refs),
        ("logs", log_refs),
    ):
        for row in rows:
            k = (row["k"] or "").strip()
            if not k:
                continue
            merged.setdefault(k, {"users": 0, "touches": 0, "logs": 0})
            merged[k][src] = int(row["cnt"])

    def is_user_referral(key: str) -> bool:
        suffix = key[4:] if key.startswith("ref_") else key
        return suffix.isdigit() and len(suffix) >= 9

    missing_campaign: list[tuple[str, dict[str, int]]] = []
    missing_user: list[tuple[str, dict[str, int]]] = []
    for key, counts in sorted(merged.items(), key=lambda x: -sum(x[1].values())):
        lookup = key[4:] if key.startswith("ref_") else key
        if lookup in ref_set:
            continue
        if is_user_referral(key):
            if show_user_refs:
                missing_user.append((key, counts))
        else:
            missing_campaign.append((key, counts))

    logger.info("=== ref_keys в справочнике: %s ===", len(ref_set))
    logger.info("")
    logger.info(
        "=== Кампании в логах/касаниях, но НЕТ в ref_keys: %s ===",
        len(missing_campaign),
    )
    for key, c in missing_campaign:
        logger.info(
            "  %s  users=%s touches=%s logs=%s",
            key,
            c["users"],
            c["touches"],
            c["logs"],
        )

    logger.info("")
    logger.info(
        "=== Пользовательские ref (telegram id), не в ref_keys: %s ===",
        len(missing_user) if show_user_refs else "(скрыто, --show-user-refs)",
    )
    if show_user_refs:
        for key, c in missing_user[:50]:
            logger.info(
                "  %s  users=%s touches=%s logs=%s",
                key,
                c["users"],
                c["touches"],
                c["logs"],
            )
        if len(missing_user) > 50:
            logger.info("  ... ещё %s", len(missing_user) - 50)

    logger.info("")
    logger.info("=== pay_last_touch_key: шаги checkout (стоит пересчитать backfill) ===")
    async with storage.get_connection() as conn:
        checkout = await conn.fetch(
            """
            SELECT pay_last_touch_key, COUNT(*) AS cnt
            FROM orders
            WHERE pay_last_touch_key LIKE 'payment_select_%'
               OR pay_last_touch_key LIKE 'payment_currency_%'
            GROUP BY pay_last_touch_key
            ORDER BY cnt DESC
            """
        )
    for row in checkout:
        logger.info("  %s: %s", row["pay_last_touch_key"], row["cnt"])

    if hasattr(storage, "close"):
        await storage.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--show-user-refs",
        action="store_true",
        help="Показать ref_<telegram_id> (рефералы пользователей, не кампании)",
    )
    args = p.parse_args()
    asyncio.run(run(show_user_refs=args.show_user_refs))


if __name__ == "__main__":
    main()
