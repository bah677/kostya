#!/usr/bin/env python3
"""
Разовый анонс «дайджест и цитаты в личку».

Запуск из dev с prod .env (не зеркалится на prod — exclude в deploy).

  cd /home/appuser/dev/kostya/club

  ./venv/bin/python scripts/send_outreach_dm_announcement.py --dry-run
  ./venv/bin/python scripts/send_outreach_dm_announcement.py --admins
  ./venv/bin/python scripts/send_outreach_dm_announcement.py --group --topic-id 5809
  ./venv/bin/python scripts/send_outreach_dm_announcement.py --all-licensed
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date
from typing import List, Optional, Set

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.texts import ru_outreach_dm_announce as txt
from bot.utils.telegram_errors import format_exception, is_topic_closed_error
from bot.utils.telegram_html import split_telegram_html_message_chunks
from bot.utils.telegram_identity import resolve_telegram_bot_username
from config import load_config
from storage.user_storage import UserStorage

logger = logging.getLogger("send_outreach_dm_announcement")

DEFAULT_PROD_ENV = "/home/appuser/club/.env"
# Топик «Дайджест» — разумный дефолт для анонса про дайджесты.
DEFAULT_TOPIC_ID = 5809


def _load_config(env_file: str):
    from dotenv import load_dotenv

    load_dotenv(env_file, override=True)
    return load_config()


def _keyboard(bot_username: str) -> InlineKeyboardMarkup:
    url = f"https://t.me/{bot_username}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=txt.BTN_OPEN_BOT_DM, url=url)]
        ]
    )


def _html(*, test: bool) -> str:
    body = txt.OUTREACH_DM_ANNOUNCE_HTML.strip()
    if test:
        return f"<b>[ТЕСТ]</b>\n\n{body}"
    return body


async def _collect_admin_ids(storage: UserStorage, super_admin_id: int) -> List[int]:
    ids: Set[int] = set()
    for row in await storage.list_telegram_admin_ids():
        uid = int(row["telegram_user_id"])
        if uid > 0:
            ids.add(uid)
    if super_admin_id > 0:
        ids.add(super_admin_id)
    return sorted(ids)


async def _send_dm(
    bot: Bot,
    *,
    chat_id: int,
    html: str,
    keyboard: Optional[InlineKeyboardMarkup] = None,
) -> bool:
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=html,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        return True
    except Exception as e:
        logger.error("send DM uid=%s: %s", chat_id, e)
        return False


async def _post_to_topic(
    bot: Bot,
    chat_id: int,
    topic_id: int,
    html: str,
    keyboard: InlineKeyboardMarkup,
) -> None:
    chunks = split_telegram_html_message_chunks(html, max_len=3800)
    for i, chunk in enumerate(chunks):
        await bot.send_message(
            chat_id=chat_id,
            message_thread_id=topic_id,
            text=chunk,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=keyboard if i == len(chunks) - 1 else None,
        )


async def _send_to_group_topic(
    bot: Bot,
    *,
    chat_id: int,
    topic_id: int,
    html: str,
    keyboard: InlineKeyboardMarkup,
) -> bool:
    reopened = False
    try:
        try:
            await _post_to_topic(bot, chat_id, topic_id, html, keyboard)
            return True
        except Exception as e:
            if not is_topic_closed_error(e):
                logger.error("send group topic %s: %s", topic_id, e)
                return False
            await bot.reopen_forum_topic(chat_id=chat_id, message_thread_id=topic_id)
            reopened = True
            await _post_to_topic(bot, chat_id, topic_id, html, keyboard)
            return True
    except Exception as e:
        logger.error("send group topic %s: %s", topic_id, format_exception(e))
        return False
    finally:
        if reopened:
            try:
                await bot.close_forum_topic(chat_id=chat_id, message_thread_id=topic_id)
            except Exception as e:
                logger.warning("close topic %s: %s", topic_id, e)


async def run(args: argparse.Namespace) -> int:
    cfg = _load_config(args.env_file)
    if not cfg.MIRON_BOT_TOKEN:
        logger.error("MIRON_BOT_TOKEN не задан в %s", args.env_file)
        return 1

    bot = Bot(token=cfg.MIRON_BOT_TOKEN)
    storage = UserStorage(cfg.database_url)

    try:
        username = await resolve_telegram_bot_username(bot)
        if not username:
            logger.error("Не удалось определить username бота")
            return 1
        keyboard = _keyboard(username)

        if args.dry_run:
            print("=== DRY RUN ===")
            print(f"env: {args.env_file}")
            print(f"bot: @{username}")
            print(f"CLUB_GROUP_ID: {cfg.CLUB_GROUP_ID}")
            print(f"topic_id: {args.topic_id}")
            print()
            print(_html(test=False))
            return 0

        if args.admins:
            admin_ids = await _collect_admin_ids(storage, int(cfg.SUPER_ADMIN_ID or 0))
            if not admin_ids:
                logger.error("Список админов пуст")
                return 1
            html = _html(test=True)
            ok_count = 0
            for uid in admin_ids:
                if await _send_dm(bot, chat_id=uid, html=html, keyboard=keyboard):
                    ok_count += 1
            print(f"Готово: тест {ok_count}/{len(admin_ids)} админам")
            return 0 if ok_count else 1

        if args.group:
            group_id = int(cfg.CLUB_GROUP_ID or 0)
            if not group_id:
                logger.error("CLUB_GROUP_ID не задан")
                return 1
            ok = await _send_to_group_topic(
                bot,
                chat_id=group_id,
                topic_id=int(args.topic_id),
                html=_html(test=False),
                keyboard=keyboard,
            )
            if ok:
                print(f"Готово: анонс в группу {group_id}, топик {args.topic_id}")
                return 0
            return 1

        if args.all_licensed:
            recipients = await storage.list_user_ids_with_active_license()
            today = date.today()
            sent = skipped = failed = 0
            html = _html(test=False)
            for uid in recipients:
                claimed = await storage.try_claim_subscription_outreach(
                    uid, txt.OUTREACH_DM_INTRO_SLUG, today
                )
                if not claimed:
                    skipped += 1
                    continue
                if await _send_dm(bot, chat_id=uid, html=html, keyboard=keyboard):
                    sent += 1
                else:
                    failed += 1
                await asyncio.sleep(0.35)
            print(
                f"Готово: intro DM sent={sent} skip={skipped} fail={failed} "
                f"total={len(recipients)}"
            )
            return 0 if failed == 0 or sent > 0 else 1

        logger.error("Укажите --admins, --group, --all-licensed или --dry-run")
        return 1
    finally:
        await bot.session.close()
        await storage.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Разовый анонс дайджеста/цитат в личку"
    )
    parser.add_argument("--env-file", default=DEFAULT_PROD_ENV)
    parser.add_argument("--admins", action="store_true")
    parser.add_argument("--group", action="store_true")
    parser.add_argument("--all-licensed", action="store_true")
    parser.add_argument("--topic-id", type=int, default=DEFAULT_TOPIC_ID)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    modes = (args.admins, args.group, args.all_licensed, args.dry_run)
    if sum(bool(x) for x in modes) != 1:
        parser.error("Ровно один режим: --admins | --group | --all-licensed | --dry-run")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
