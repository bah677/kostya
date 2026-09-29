#!/usr/bin/env python3
"""Повторная выдача gift-ticket инвайтов (после flood / билет без кнопки).

Запуск из dev с prod .env:

  cd /home/appuser/dev/kostya/club
  ./venv/bin/python scripts/resend_gift_ticket_invites.py --dry-run
  ./venv/bin/python scripts/resend_gift_ticket_invites.py --send
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot
from dotenv import load_dotenv

from bot.features.club_group import ClubGroupFeature
from config import load_config
from storage.user_storage import UserStorage

logger = logging.getLogger("resend_gift_ticket_invites")
DEFAULT_PROD_ENV = "/home/appuser/club/.env"

# 29.09.2026: Failed to create invite + не было успешного Gift ticket invite
# (исключены 3 с blocked bot и 2, кто позже получил кнопку)
UIDS_2026_09_29 = [
    335095217,
    342517180,
    361981504,
    368466533,
    474795914,
    478631152,
    481059967,
    532560060,
    568148750,
    656662589,
    693073538,
    701996245,
    971174791,
    999236215,
    1026550524,
    1166099876,
    1255525145,
    1311897714,
    1464556233,
    1653528471,
    1673286520,
    1699946119,
    1985703498,
    2007702934,
    2080098984,
    2089809466,
    5065643555,
    5170748414,
    6048032239,
    6110275064,
    6618601206,
    6900761327,
    7750462521,
    7768838533,
]

PAUSE_SEC = 2.0


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=DEFAULT_PROD_ENV)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--pause", type=float, default=PAUSE_SEC)
    args = parser.parse_args()
    if not args.dry_run and not args.send:
        parser.error("укажите --dry-run или --send")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    load_dotenv(args.env, override=True)
    cfg = load_config()
    uids = list(UIDS_2026_09_29)
    logger.info("uids=%s dry_run=%s pause=%.1fs", len(uids), args.dry_run, args.pause)

    if args.dry_run:
        for uid in uids:
            logger.info("would send gift ticket invite uid=%s", uid)
        return

    token = cfg.MIRON_BOT_TOKEN
    if not token:
        raise SystemExit("нет MIRON_BOT_TOKEN")
    bot = Bot(token=token)
    storage = UserStorage(cfg.database_url)
    await storage.connect()
    club = ClubGroupFeature(user_storage=storage, bot=bot)
    ok_n = 0
    fail_n = 0
    try:
        for i, uid in enumerate(uids, 1):
            try:
                sent = await club.send_gift_ticket_invite(uid)
                if sent:
                    ok_n += 1
                    logger.info("[%s/%s] ok uid=%s", i, len(uids), uid)
                else:
                    fail_n += 1
                    logger.error("[%s/%s] FAIL uid=%s", i, len(uids), uid)
            except Exception as e:
                fail_n += 1
                logger.exception("[%s/%s] ERROR uid=%s: %s", i, len(uids), uid, e)
            await asyncio.sleep(args.pause)
    finally:
        await storage.close()
        await bot.session.close()

    logger.info("done ok=%s fail=%s", ok_n, fail_n)


if __name__ == "__main__":
    asyncio.run(main())
