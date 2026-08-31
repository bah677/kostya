#!/usr/bin/env python3
"""
Вернуть просьбу в общий пул и опубликовать в топике клуба как новую.

Запуск из dev с prod .env (как send_angel_announcement.py), деплой не нужен.

Примеры:

  cd /home/appuser/dev/kostya/club

  # Предпросмотр
  ./venv/bin/python scripts/reopen_wish_to_pool.py --wish-id 39 --dry-run

  # Сразу
  ./venv/bin/python scripts/reopen_wish_to_pool.py --wish-id 39

  # Завтра в 09:00 МСК (скрипт ждёт и выполняет)
  nohup ./venv/bin/python scripts/reopen_wish_to_pool.py \\
    --wish-id 39 --wait-until 2026-09-01T09:00:00+03:00 \\
    >> log/reopen_wish_39.log 2>&1 &
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot

from bot.services import wish_board_notify as wb_notify
from bot.texts import ru_wish_board as wb_txt
from config import load_config
from storage.user_storage import UserStorage

logger = logging.getLogger("reopen_wish_to_pool")

DEFAULT_PROD_ENV = "/home/appuser/club/.env"
_MSK = ZoneInfo("Europe/Moscow")


def _load_config(env_file: str):
    from dotenv import load_dotenv

    load_dotenv(env_file, override=True)
    return load_config()


def _parse_wait_until(raw: str) -> datetime:
    text = (raw or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_MSK)
    return dt.astimezone(_MSK)


async def _sleep_until(target: datetime) -> None:
    now = datetime.now(_MSK)
    delay = (target - now).total_seconds()
    if delay <= 0:
        logger.info("wait-until %s уже прошло — выполняем сразу", target.isoformat())
        return
    logger.info(
        "Ждём до %s МСК (~%.0f мин)",
        target.strftime("%d.%m.%Y %H:%M"),
        delay / 60,
    )
    await asyncio.sleep(delay)


async def reopen_and_post(
    *,
    storage: UserStorage,
    bot: Bot,
    wish_id: int,
    dry_run: bool,
    notify_requester: bool,
    notify_donor: bool,
) -> int:
    wish = await storage.wish_get(wish_id)
    if not wish:
        logger.error("Просьба #%s не найдена", wish_id)
        return 1

    status = (wish.get("status") or "").strip()
    if status != "taken":
        logger.error(
            "Просьба #%s в статусе %r — нужен taken", wish_id, status
        )
        return 1

    donor_id = wish.get("donor_user_id")
    requester_id = int(wish.get("requester_user_id") or 0)
    logger.info(
        "wish #%s: taken by donor=%s, requester=%s, digest_msg=%s",
        wish_id,
        donor_id,
        requester_id,
        wish.get("digest_notice_message_id"),
    )

    if dry_run:
        logger.info(
            "[dry-run] reopen → open, clear digest_notice, post as new, notify=%s/%s",
            notify_requester,
            notify_donor,
        )
        return 0

    reopened = await storage.wish_admin_reopen_to_pool(
        wish_id, clear_digest_notice=True
    )
    if not reopened:
        logger.error("Не удалось вернуть просьбу #%s в пул", wish_id)
        return 1

    previous_donor = reopened.get("_previous_donor_id") or donor_id

    await wb_notify.post_admin_lifecycle(
        bot,
        event=wb_txt.ADM_EVENT_ADMIN_REOPENED,
        wish=reopened,
        extra="повторная публикация в группе",
    )

    posted = await wb_notify.post_digest_wish(bot, storage, reopened)
    if not posted:
        logger.error(
            "Просьба #%s в пуле, но пост в группу не удался — проверьте топик",
            wish_id,
        )
        return 2

    logger.info(
        "Просьба #%s опубликована заново, digest_msg=%s",
        wish_id,
        reopened.get("digest_notice_message_id"),
    )

    if notify_requester and requester_id:
        await wb_notify.notify_user_html(
            bot,
            requester_id,
            wb_txt.NOTIFY_ADMIN_REOPENED_REQUESTER_HTML,
        )

    if notify_donor and previous_donor:
        await wb_notify.notify_user_html(
            bot,
            int(previous_donor),
            wb_txt.NOTIFY_ADMIN_REOPENED_DONOR_HTML.format(wish_id=wish_id),
        )

    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Вернуть просьбу в общий пул и опубликовать как новую"
    )
    parser.add_argument("--wish-id", type=int, required=True)
    parser.add_argument("--env-file", default=DEFAULT_PROD_ENV)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--wait-until",
        help="ISO datetime (МСК если без TZ), например 2026-09-01T09:00:00+03:00",
    )
    parser.add_argument("--no-notify-requester", action="store_true")
    parser.add_argument("--no-notify-donor", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.wait_until:
        await _sleep_until(_parse_wait_until(args.wait_until))

    cfg = _load_config(args.env_file)
    token = (cfg.MIRON_BOT_TOKEN or "").strip()
    if not token:
        logger.error("MIRON_BOT_TOKEN не задан в %s", args.env_file)
        return 1
    if not cfg.wish_board_active:
        logger.error("wish_board не активна (топики / WISH_BOARD_ENABLED)")
        return 1

    storage = UserStorage(cfg.database_url)
    await storage.initialize()
    bot = Bot(token=token)
    try:
        return await reopen_and_post(
            storage=storage,
            bot=bot,
            wish_id=int(args.wish_id),
            dry_run=bool(args.dry_run),
            notify_requester=not args.no_notify_requester,
            notify_donor=not args.no_notify_donor,
        )
    finally:
        await bot.session.close()
        await storage.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
