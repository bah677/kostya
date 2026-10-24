#!/usr/bin/env python3
"""
Перерисовка устаревших карточек просьб в теме клубной группы.

ЗАЧЕМ. Ручное подтверждение просьбы (_confirm_done) правило карточку, а
автоматическое закрытие по оплате подписки — нет, хотя через него идёт почти
весь поток. В результате на 24 октября 2026 в группе висели 18 карточек с
25 июня по 28 августа: заголовок «нужна помощь» и рабочая ссылка
«Откликнуться» на просьбах, которые давно закрыты. Человек жал её и попадал
в никуда.

Сама причина устранена в bot/payments/fulfillment.py — новые просьбы
закрываются правильно. Этот скрипт чинит накопленное.

ИДЕМПОТЕНТНОСТЬ. Берём только те карточки, у которых последняя правка старше
последней смены статуса (или правок не было вовсе). edit_digest_wish_post
сам пишет событие wb_post_edited, поэтому на следующем запуске исправленные
карточки уже не попадут в выборку.

  cd /home/appuser/dev/kostya/club
  ./venv/bin/python3 scripts/refresh_wish_board_digest_cards.py --env-file /home/appuser/club/.env --dry-run
  ./venv/bin/python3 scripts/refresh_wish_board_digest_cards.py --env-file /home/appuser/club/.env --apply
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot

from config import load_config
from storage.user_storage import UserStorage

logger = logging.getLogger("refresh_wish_board_digest_cards")

#: Статусы, карточка которых должна быть «снята» — закрытые и снятые просьбы.
TERMINAL_STATUSES = ("completed", "cancelled", "rejected", "expired")

#: События, которые меняют вид карточки.
STATUS_EVENTS = (
    "taken",
    "released",
    "taken_timeout",
    "done_pending",
    "completed",
    "cancelled",
    "rejected",
    "expired",
)

_STALE_SQL = """
SELECT w.*,
       last_status.at  AS last_status_at,
       last_edit.at    AS last_edit_at
FROM wish_requests w
LEFT JOIN LATERAL (
    SELECT max(e.created_at) AS at FROM wish_events e
    WHERE e.wish_id = w.id AND e.event_type = ANY($1::text[])
) last_status ON TRUE
LEFT JOIN LATERAL (
    SELECT max(p.created_at) AS at FROM wish_events p
    WHERE p.wish_id = w.id AND p.event_type = 'wb_post_edited'
) last_edit ON TRUE
WHERE NOT w.is_test
  AND w.digest_notice_message_id IS NOT NULL
  AND w.status = ANY($2::varchar[])
  AND last_status.at IS NOT NULL
  AND (last_edit.at IS NULL OR last_edit.at < last_status.at)
ORDER BY w.id
"""


def _load_config(env_file: str):
    if env_file:
        from dotenv import load_dotenv

        load_dotenv(env_file, override=True)
    return load_config()


async def collect_stale(storage, statuses: List[str]) -> List[Dict[str, Any]]:
    async with storage.get_connection() as conn:
        rows = await conn.fetch(_STALE_SQL, list(STATUS_EVENTS), statuses)
    return [dict(r) for r in rows]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default="")
    ap.add_argument(
        "--statuses",
        default=",".join(TERMINAL_STATUSES),
        help="какие статусы перерисовывать (через запятую)",
    )
    ap.add_argument("--pause", type=float, default=1.2,
                    help="пауза между правками, сек — Telegram не любит спешку")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = _load_config(args.env_file)
    statuses = [s.strip() for s in args.statuses.split(",") if s.strip()]

    storage = UserStorage(cfg.database_url)
    await storage.initialize()
    bot = None
    try:
        stale = await collect_stale(storage, statuses)
        if not stale:
            print("Устаревших карточек нет — всё актуально.")
            return 0

        print(f"\nУстаревших карточек: {len(stale)}\n")
        for w in stale:
            print(
                f"  №{w['id']:<4} {str(w['status']):<10} пост {w['digest_notice_message_id']:<7}"
                f" статус менялся {str(w['last_status_at'])[:16]},"
                f" карточка правилась {str(w['last_edit_at'])[:16] if w['last_edit_at'] else 'ни разу'}"
            )

        if args.dry_run:
            print("\n--dry-run: ничего не отправлено.")
            return 0

        token = (cfg.MIRON_BOT_TOKEN or "").strip()
        if not token:
            logger.error("MIRON_BOT_TOKEN не задан — перерисовать карточки нечем")
            return 1
        bot = Bot(token=token)

        from bot.services.wish_board_notify import edit_digest_wish_post

        ok = failed = 0
        for w in stale:
            try:
                done = await edit_digest_wish_post(bot, storage, w, status=w["status"])
            except Exception as e:
                done = False
                logger.warning("карточка №%s: %s", w["id"], e)
            if done:
                ok += 1
                print(f"  ✅ №{w['id']} → «{w['status']}»")
            else:
                failed += 1
                print(f"  ❌ №{w['id']} не перерисована")
            # Между правками держим паузу: 18 подряд Telegram примет за флуд.
            await asyncio.sleep(max(0.0, args.pause))

        print(f"\nГотово: перерисовано {ok}, не удалось {failed}.")
        return 0 if failed == 0 else 1
    finally:
        if bot is not None:
            await bot.session.close()
        await storage.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
