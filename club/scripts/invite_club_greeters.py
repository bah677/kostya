#!/usr/bin/env python3
"""
Импорт топ встречающих из истории ответов + приглашение в личку.

Запуск из dev с prod .env:

  cd /home/appuser/dev/kostya/club
  ./venv/bin/python scripts/invite_club_greeters.py --dry-run
  ./venv/bin/python scripts/invite_club_greeters.py --admins   # только админам текст
  ./venv/bin/python scripts/invite_club_greeters.py --send     # пул + DM кандидатам + копия админам
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import List, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.texts import ru_gift_wave as txt
from config import load_config
from storage.user_storage import UserStorage

logger = logging.getLogger("invite_club_greeters")

DEFAULT_PROD_ENV = "/home/appuser/club/.env"
# Высокочастотные аккаунты / не кандидаты в «сестринский» пул
EXCLUDE_USER_IDS = {
    8552051262,  # Анастасия — служебный/нетипичный топ
}


def _load_config(env_file: str):
    from dotenv import load_dotenv

    load_dotenv(env_file, override=True)
    return load_config()


async def fetch_top_greeter_candidates(
    storage: UserStorage, *, limit: int = 23
) -> List[Tuple[int, int, str]]:
    from config import config

    chat_id = int(config.CLUB_GROUP_ID)
    async with storage.pool.acquire() as conn:
        admin_ids = [
            int(r["telegram_user_id"])
            for r in await conn.fetch(
                "SELECT telegram_user_id FROM admins WHERE telegram_user_id IS NOT NULL"
            )
        ]
        exclude = set(admin_ids) | EXCLUDE_USER_IDS | {int(config.SUPER_ADMIN_ID or 0)}
        rows = await conn.fetch(
            """
            WITH replies AS (
              SELECT
                m.user_id AS greeter_id,
                COUNT(*)::int AS n
              FROM messages m
              WHERE m.chat_id = $1
                AND m.chat_type IN ('group', 'supergroup')
                AND m.sender_type = 'user'
                AND m.created_at >= NOW() - INTERVAL '180 days'
                AND m.raw_data ? 'reply_to_message'
                AND (m.raw_data->'reply_to_message'->>'message_id') IS NOT NULL
                AND COALESCE((m.raw_data->>'message_thread_id')::bigint, -1)
                    IS DISTINCT FROM
                    (m.raw_data->'reply_to_message'->>'message_id')::bigint
                AND COALESCE(
                    (m.raw_data->'reply_to_message'->>'message_id')::bigint, 0
                ) > 10
              GROUP BY m.user_id
            )
            SELECT r.greeter_id, r.n, u.username, u.first_name
            FROM replies r
            LEFT JOIN users u ON u.user_id = r.greeter_id
            WHERE NOT (r.greeter_id = ANY($2::bigint[]))
            ORDER BY r.n DESC
            LIMIT $3
            """,
            chat_id,
            list(exclude) or [0],
            limit,
        )
    out: List[Tuple[int, int, str]] = []
    for r in rows:
        uid = int(r["greeter_id"])
        label = r["username"] or r["first_name"] or str(uid)
        out.append((uid, int(r["n"]), str(label)))
    return out


def _invite_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=txt.BTN_GREETER_ACCEPT_INVITE,
                    callback_data="gw:invite:yes",
                )
            ],
            [
                InlineKeyboardButton(
                    text=txt.BTN_GREETER_DECLINE_INVITE,
                    callback_data="gw:invite:no",
                )
            ],
        ]
    )


async def _send(bot: Bot, chat_id: int, html: str, keyboard=None) -> bool:
    try:
        await bot.send_message(
            chat_id,
            html,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        return True
    except Exception as e:
        logger.error("send %s: %s", chat_id, e)
        return False


async def run(args: argparse.Namespace) -> int:
    cfg = _load_config(args.env_file)
    bot = Bot(token=cfg.MIRON_BOT_TOKEN)
    storage = UserStorage(cfg.database_url)
    await storage.connect()
    try:
        candidates = await fetch_top_greeter_candidates(storage, limit=args.limit)
        lines = [
            f"• <code>{uid}</code> {label} — ответов≈{n}"
            for uid, n, label in candidates
        ]
        preview = (
            "<b>[КОНТРОЛЬ] Приглашение встречающих</b>\n\n"
            f"Кандидатов: {len(candidates)}\n"
            + "\n".join(lines)
            + "\n\n--- текст участникам ---\n\n"
            + txt.GREETER_INVITE_HTML
        )

        if args.dry_run:
            print(preview)
            return 0

        admin_ids: Set[int] = set()
        for row in await storage.list_telegram_admin_ids():
            admin_ids.add(int(row["telegram_user_id"]))
        if cfg.SUPER_ADMIN_ID:
            admin_ids.add(int(cfg.SUPER_ADMIN_ID))

        if args.admins or args.send:
            for aid in sorted(admin_ids):
                await _send(bot, aid, preview)
                await asyncio.sleep(0.3)

        if args.admins and not args.send:
            print(f"Отправлено админам: {len(admin_ids)}")
            return 0

        if not args.send:
            print("Укажите --send или --admins")
            return 1

        # В пул сразу (active), кнопка «пока не могу» снимет
        ok = 0
        for uid, _n, label in candidates:
            await storage.upsert_club_greeter(uid, active=True, capacity=3)
            if await _send(bot, uid, txt.GREETER_INVITE_HTML, _invite_keyboard()):
                ok += 1
                logger.info("invited %s (%s)", uid, label)
            await asyncio.sleep(0.35)
        print(f"В пуле + DM: {ok}/{len(candidates)}; админам — копия списка")
        return 0
    finally:
        await bot.session.close()
        await storage.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--env-file", default=DEFAULT_PROD_ENV)
    p.add_argument("--limit", type=int, default=23)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--admins", action="store_true", help="Только контроль админам")
    p.add_argument("--send", action="store_true", help="Пул + DM кандидатам + админам")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
