#!/usr/bin/env python3
"""Выгрузка когорт Библии (Б1–Б5) для этапа 2 рассылки gift-2026-09.

Список собирается на стороне клуба + БД Библии, затем передаётся в
mailing_campaigns библейского бота (Т2 + кнопка t.me/Talk_God_Bot?start=gift_bib).

Пример:
  cd /home/appuser/dev/kostya/club
  python3 scripts/export_gift_biblia_cohorts.py --cohort B1 --out /tmp/gift_b1.txt
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

MSK = ZoneInfo("Europe/Moscow")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", required=True, choices=["B1", "B2", "B3", "B4", "B5"])
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    import asyncpg
    from bot.services.biblia_club_campaign_report import (
        biblia_db_configured,
        create_biblia_pool,
    )
    from config import config
    from storage.user_storage import UserStorage

    if not biblia_db_configured(config):
        raise SystemExit("BIBLIA_DB_* не настроены в club .env")

    club = UserStorage(config.DATABASE_URL)
    await club.connect()
    biblia = await create_biblia_pool(config)

    try:
        async with biblia.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH act AS (
                  SELECT user_id,
                         count(DISTINCT created_at::date)
                           FILTER (WHERE created_at > NOW() - interval '60 days') AS d60,
                         max(created_at) AS last
                  FROM messages WHERE sender_type = 'user' GROUP BY 1
                ),
                don AS (SELECT DISTINCT user_id FROM payments WHERE status = 'succeeded'),
                pr  AS (
                  SELECT user_id, count(*) AS n FROM token_usage
                  WHERE request_kind LIKE 'personal_prayer_compose%' GROUP BY 1
                )
                SELECT u.user_id, COALESCE(a.d60, 0) AS d60, a.last,
                       (d.user_id IS NOT NULL) AS donor, COALESCE(pr.n, 0) AS prayers
                FROM users u
                LEFT JOIN act a ON a.user_id = u.user_id
                LEFT JOIN don d ON d.user_id = u.user_id
                LEFT JOIN pr ON pr.user_id = u.user_id
                WHERE COALESCE(u.is_active, TRUE)
                  AND u.bot_blocked_at IS NULL
                  AND NOT COALESCE(u.is_banned, FALSE)
                  AND COALESCE(u.mailing_consent, TRUE)
                """
            )

        # исключить club paid/license + уже получивших Т1 / заявку
        async with club.get_connection() as conn:
            excluded = await conn.fetch(
                """
                SELECT user_id FROM payments WHERE status = 'succeeded'
                UNION
                SELECT user_id FROM license
                UNION
                SELECT user_id FROM gift_mailing_sent WHERE campaign = 'gift-2026-09'
                UNION
                SELECT user_id FROM gift_application WHERE campaign = 'gift-2026-09'
                """
            )
        excl = {int(r["user_id"]) for r in excluded}

        now = datetime.now(MSK)
        out_ids = []
        for r in rows:
            uid = int(r["user_id"])
            if uid in excl:
                continue
            last = r["last"]
            d60 = int(r["d60"] or 0)
            donor = bool(r["donor"])
            prayers = int(r["prayers"] or 0)
            if last is not None and last.tzinfo is None:
                last = last.replace(tzinfo=MSK)
            days_ago = (now - last.astimezone(MSK)).days if last else 10_000

            cohort = None
            if donor and days_ago <= 60:
                cohort = "B1"
            elif days_ago <= 60 and (d60 >= 4 or prayers >= 2):
                cohort = "B2"
            elif days_ago <= 60:
                cohort = "B3"
            elif 61 <= days_ago <= 180:
                cohort = "B4"
            else:
                cohort = "B5"

            if cohort == args.cohort:
                out_ids.append(uid)

        if args.limit > 0:
            out_ids = out_ids[: args.limit]

        Path(args.out).write_text(
            "\n".join(str(i) for i in out_ids) + ("\n" if out_ids else ""),
            encoding="utf-8",
        )
        print(f"{args.cohort}: {len(out_ids)} -> {args.out}")
    finally:
        await biblia.close()
        await club.close()


if __name__ == "__main__":
    asyncio.run(main())
