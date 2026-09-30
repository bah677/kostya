#!/usr/bin/env python3
"""Синк пула встречающих + опционально добавить кандидатов и позвать на встречу.

  cd /home/appuser/dev/kostya/club
  ./venv/bin/python scripts/sync_greeter_pool.py --dry-run
  ./venv/bin/python scripts/sync_greeter_pool.py --deactivate
  ./venv/bin/python scripts/sync_greeter_pool.py --add-top 10 --invite-meeting
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

from bot.services import greeter_meeting_service as gm
from bot.services.club_greeter_service import (
    fetch_greeter_pool_candidates,
    sync_greeter_pool_licenses,
)
from config import load_config
from storage.user_storage import UserStorage

logger = logging.getLogger("sync_greeter_pool")
DEFAULT_PROD_ENV = "/home/appuser/club/.env"


def _label(row: dict) -> str:
    un = row.get("username")
    name = " ".join(
        p for p in (row.get("first_name") or "", row.get("last_name") or "") if p
    )
    if un and name:
        return f"@{un} ({name})"
    return f"@{un}" if un else (name or str(row.get("user_id")))


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--env", default=DEFAULT_PROD_ENV)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--deactivate", action="store_true", help="Снять без лицензии")
    p.add_argument(
        "--add-top",
        type=int,
        default=0,
        help="Добавить в пул топ-N кандидатов",
    )
    p.add_argument(
        "--invite-meeting",
        action="store_true",
        help="Отправить им приглашение на закрытую встречу",
    )
    p.add_argument("--pause", type=float, default=0.4)
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    load_dotenv(args.env, override=True)
    cfg = load_config()
    storage = UserStorage(cfg.database_url)
    await storage.connect()
    bot = None
    try:
        if args.deactivate or args.dry_run:
            if args.dry_run and not args.deactivate:
                # preview stale
                async with storage.pool.acquire() as conn:
                    stale = await conn.fetch(
                        """
                        SELECT g.user_id, u.username, u.first_name, u.last_name
                        FROM club_greeter g
                        LEFT JOIN users u ON u.user_id = g.user_id
                        WHERE g.active = TRUE
                          AND NOT EXISTS (
                            SELECT 1 FROM license l
                            WHERE l.user_id = g.user_id
                              AND l.status = 'active' AND l.expires_at > NOW()
                          )
                        """
                    )
                print(f"would deactivate: {len(stale)}")
                for r in stale:
                    print(f"  {r['user_id']} {_label(dict(r))}")
            if args.deactivate:
                removed = await sync_greeter_pool_licenses(user_storage=storage)
                print(f"deactivated: {removed}")

        cands = await fetch_greeter_pool_candidates(storage, limit=max(20, args.add_top or 20))
        print(f"\ncandidates: {len(cands)}")
        for r in cands:
            print(
                f"  {r['replies_180d']:4d} / 30d={r['msgs_30d']:3d} | "
                f"{r['user_id']} {_label(r)}"
            )

        if args.add_top <= 0:
            return

        chosen = cands[: args.add_top]
        if not chosen:
            print("nobody to add")
            return
        if args.dry_run:
            print(f"would add {len(chosen)} and invite={args.invite_meeting}")
            return

        bot = Bot(token=cfg.MIRON_BOT_TOKEN)
        added_ids = []
        for r in chosen:
            uid = int(r["user_id"])
            ok = await storage.upsert_club_greeter(uid, active=True, capacity=3)
            logger.info("upsert greeter uid=%s ok=%s %s", uid, ok, _label(r))
            if ok:
                added_ids.append(uid)
                try:
                    from bot.services.greeter_room_service import on_greeter_activated

                    await on_greeter_activated(bot, storage, uid)
                except Exception as e:
                    logger.warning("greeter room invite uid=%s: %s", uid, e)

        if args.invite_meeting and added_ids:
            stats = await gm.blast_invite(
                bot, storage, recipients=added_ids, pause_sec=args.pause
            )
            print(f"meeting invite: {stats}")
        print(f"added to pool: {added_ids}")
    finally:
        if bot:
            await bot.session.close()
        await storage.close()


if __name__ == "__main__":
    asyncio.run(main())
