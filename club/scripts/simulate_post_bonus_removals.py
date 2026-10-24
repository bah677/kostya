#!/usr/bin/env python3
"""
Что решит ночной крон по каждому кандидату на исключение из группы.

Сухой прогон: ничего не отправляет и не меняет. Для каждого, у кого доступ
кончается в ближайшие дни, показывает — выставим из группы или отложим
из-за живой просьбы на ДДД, и до какого числа отложим.

  cd /home/appuser/dev/kostya/club
  ./venv/bin/python3 scripts/simulate_post_bonus_removals.py --env-file /home/appuser/club/.env
  ./venv/bin/python3 scripts/simulate_post_bonus_removals.py --env-file /home/appuser/club/.env --days 14
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import load_config
from storage.user_storage import UserStorage

MSK = ZoneInfo("Europe/Moscow")

_SQL = """
SELECT l.user_id, l.license_type, l.origin, l.status,
       (timezone('Europe/Moscow', l.expires_at))::date AS exp_date,
       u.first_name, u.is_active,
       EXISTS(SELECT 1 FROM club_group_member_cache k WHERE k.user_id = l.user_id) AS in_group
FROM license l
JOIN users u ON u.user_id = l.user_id
WHERE l.status = 'active'
  AND (timezone('Europe/Moscow', l.expires_at))::date
      BETWEEN current_date - 1 AND current_date + $1::int
ORDER BY exp_date, l.user_id
"""


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default="")
    ap.add_argument("--days", type=int, default=10, help="горизонт в днях")
    args = ap.parse_args()

    if args.env_file:
        from dotenv import load_dotenv

        load_dotenv(args.env_file, override=True)
    cfg = load_config()
    grace = int(getattr(cfg, "WISH_BOARD_KICK_GRACE_DAYS", 7) or 0)

    storage = UserStorage(cfg.database_url)
    await storage.initialize()
    try:
        async with storage.get_connection() as conn:
            rows = [dict(r) for r in await conn.fetch(_SQL, int(args.days))]
        if not rows:
            print("Кандидатов на исключение в горизонте нет.")
            return 0

        print(f"\nОтсрочка по просьбе ДДД: {grace} дн. Горизонт: {args.days} дн.\n")
        print(f"{'кто':<18}{'доступ до':<12}{'тип':<14}{'решение'}")
        print("-" * 86)
        held = kicked = 0
        for r in rows:
            wish = await storage.wish_active_for_requester(int(r["user_id"]))
            exp: date = r["exp_date"]
            # Крон срабатывает после бонусного дня: считаем от даты окончания.
            kick_day = exp + timedelta(days=1)
            if wish:
                until = exp + timedelta(days=grace)
                status = str(wish.get("status") or "")
                donor = wish.get("donor_user_id")
                who = "взята" if status in ("taken", "done_pending") else "открыта"
                verdict = (
                    f"ОТЛОЖИМ до {until:%d.%m} · просьба №{wish.get('id')} ({who}"
                    + (f", даритель {donor}" if donor else "")
                    + ")"
                )
                held += 1
            else:
                verdict = f"исключим {kick_day:%d.%m}"
                kicked += 1
            name = str(r["first_name"] or "—")[:16]
            print(
                f"{name:<18}{exp:%d.%m.%Y}  {str(r['license_type'])[:12]:<14}{verdict}"
            )
        print("-" * 86)
        print(f"\nИтого в горизонте: отложим {held}, исключим {kicked}.")
        if held:
            print(
                "Отложенным уходит по одному письму на просьбу: просителю — "
                "почему он остаётся, дарителю — что человека ждут из-за него."
            )
        return 0
    finally:
        await storage.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
