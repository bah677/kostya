#!/usr/bin/env python3
"""Дослать ссылки тем, у кого билет ещё жив, а 24ч-ссылка уже сгорела.

Новая ссылка живёт до granted_at + GIFT_TICKET_TTL_DAYS (обычно 7 суток
с момента первой выдачи).

  cd /home/appuser/dev/kostya/club
  ./venv/bin/python scripts/resend_gift_ticket_remaining.py --dry-run
  ./venv/bin/python scripts/resend_gift_ticket_remaining.py --send
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot
from dotenv import load_dotenv

from bot.features.club_group import ClubGroupFeature
from bot.texts import ru_gift_application as ga_txt
from config import load_config
from storage.user_storage import UserStorage

logger = logging.getLogger("resend_gift_ticket_remaining")
DEFAULT_PROD_ENV = "/home/appuser/club/.env"
MSK = ZoneInfo("Europe/Moscow")


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--env", default=DEFAULT_PROD_ENV)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--send", action="store_true")
    p.add_argument("--pause", type=float, default=2.0)
    args = p.parse_args()
    if not args.dry_run and not args.send:
        p.error("укажите --dry-run или --send")

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    load_dotenv(args.env, override=True)
    cfg = load_config()
    ttl_days = int(getattr(cfg, "GIFT_TICKET_TTL_DAYS", 7) or 7)

    storage = UserStorage(cfg.database_url)
    await storage.connect()
    bot = None
    try:
        async with storage.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT gwm.user_id, gwm.granted_at, gwm.wave_id,
                       u.username, u.first_name
                FROM gift_wave_member gwm
                LEFT JOIN users u ON u.user_id = gwm.user_id
                WHERE gwm.status = 'granted'
                  AND gwm.granted_at IS NOT NULL
                  AND gwm.granted_at <= NOW() - INTERVAL '24 hours'
                  AND gwm.granted_at > NOW() - ($1 || ' days')::interval
                  AND NOT EXISTS (
                    SELECT 1 FROM club_group_member_cache c
                    WHERE c.user_id = gwm.user_id
                  )
                ORDER BY gwm.granted_at ASC
                """,
                str(ttl_days),
            )

        logger.info("candidates=%s ttl_days=%s", len(rows), ttl_days)
        for r in rows:
            gat = r["granted_at"]
            if gat.tzinfo is None:
                gat = gat.replace(tzinfo=MSK)
            deadline = gat + timedelta(days=ttl_days)
            left_h = (deadline - datetime.now(timezone.utc)).total_seconds() / 3600
            logger.info(
                "uid=%s @%s granted=%s deadline=%s left≈%.1fh",
                r["user_id"],
                r["username"] or "—",
                gat.isoformat(),
                deadline.isoformat(),
                left_h,
            )

        if args.dry_run:
            return

        bot = Bot(token=cfg.MIRON_BOT_TOKEN)
        club = ClubGroupFeature(user_storage=storage, bot=bot)
        ok = fail = skip = 0
        for r in rows:
            uid = int(r["user_id"])
            gat = r["granted_at"]
            if gat.tzinfo is None:
                gat = gat.replace(tzinfo=MSK)
            expire_at = gat + timedelta(days=ttl_days)
            if expire_at <= datetime.now(timezone.utc) + timedelta(minutes=30):
                skip += 1
                logger.warning("skip uid=%s — почти конец билета", uid)
                continue
            sent = await club.send_gift_ticket_invite(
                uid,
                expire_at=expire_at,
                message_html=ga_txt.T16_LINK_REFRESH_HTML,
            )
            if sent:
                ok += 1
                logger.info("ok uid=%s", uid)
            else:
                fail += 1
                logger.error("fail uid=%s", uid)
            await asyncio.sleep(args.pause)
        logger.info("done ok=%s fail=%s skip=%s", ok, fail, skip)
    finally:
        if bot:
            await bot.session.close()
        await storage.close()


if __name__ == "__main__":
    asyncio.run(main())
