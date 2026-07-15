#!/usr/bin/env python3
"""
Разовый анонс «Стать ангелом» в клубную группу.

Запуск только из dev, с prod .env (токен бота и CLUB_GROUP_ID прода).
Не катится на prod — см. exclude в scripts/deploy_prod.sh.

Примеры (основной бот club может работать — коллизии нет):

  cd /home/appuser/dev/club

  # Предпросмотр
  ./venv/bin/python scripts/send_angel_announcement.py --dry-run

  # Шаг 1: тест админам в личку
  ./venv/bin/python scripts/send_angel_announcement.py --admins

  # Шаг 2: в группу, топик 1503 (после проверки теста)
  ./venv/bin/python scripts/send_angel_announcement.py --group --topic-id 1503
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import List, Optional, Set

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.services.wish_board_deeplink import build_angel_pool_deeplink
from bot.texts import ru_angel_pool as ap_txt
from bot.utils.telegram_errors import format_exception, is_topic_closed_error
from bot.utils.telegram_html import split_telegram_html_message_chunks
from bot.utils.telegram_identity import resolve_telegram_bot_username
from config import load_config
from storage.user_storage import UserStorage

logger = logging.getLogger("send_angel_announcement")

DEFAULT_PROD_ENV = "/home/appuser/club/.env"
DEFAULT_TOPIC_ID = 1503


def _load_config(env_file: str):
    from dotenv import load_dotenv

    load_dotenv(env_file, override=True)
    return load_config()


def _announcement_keyboard(deeplink: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=ap_txt.BTN_ANGEL,
                    url=deeplink,
                    style="success",
                )
            ]
        ]
    )


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
    keyboard: InlineKeyboardMarkup,
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


async def _send_to_group_topic(
    bot: Bot,
    *,
    chat_id: int,
    topic_id: int,
    html: str,
    keyboard: InlineKeyboardMarkup,
) -> bool:
    """HTML + кнопка в топик (reopen при TOPIC_CLOSED)."""
    reopened = False
    try:
        try:
            await _post_to_topic(bot, chat_id, topic_id, html, keyboard)
            return True
        except Exception as e:
            if not is_topic_closed_error(e):
                logger.error("send group topic %s: %s", topic_id, e)
                return False
            logger.info("topic %s closed, reopen → send", topic_id)
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
            logger.error(
                "Не удалось определить username бота (get_me / TELEGRAM_BOT_USERNAME)"
            )
            return 1

        deeplink = build_angel_pool_deeplink(username)
        keyboard = _announcement_keyboard(deeplink)

        if args.dry_run:
            print("=== DRY RUN ===")
            print(f"env: {args.env_file}")
            print(f"bot: @{username}")
            print(f"deeplink: {deeplink}")
            print(f"CLUB_GROUP_ID: {cfg.CLUB_GROUP_ID}")
            print(f"topic_id: {args.topic_id}")
            print()
            print("--- admins (test) ---")
            print(ap_txt.build_group_announcement_html(test=True))
            print()
            print("--- group ---")
            print(ap_txt.build_group_announcement_html(test=False))
            print()
            print(f"button: {ap_txt.BTN_ANGEL} -> {deeplink}")
            return 0

        if args.admins:
            admin_ids = await _collect_admin_ids(storage, int(cfg.SUPER_ADMIN_ID or 0))
            if not admin_ids:
                logger.error("Список админов пуст")
                return 1
            html = ap_txt.build_group_announcement_html(test=True)
            ok_count = 0
            for uid in admin_ids:
                if await _send_dm(bot, chat_id=uid, html=html, keyboard=keyboard):
                    ok_count += 1
                    logger.info("OK test DM -> %s", uid)
            logger.info("Тест админам: %s/%s", ok_count, len(admin_ids))
            if ok_count == 0:
                return 1
            print(
                f"Готово: тест отправлен {ok_count} админам. "
                f"Проверьте личку и кнопку, затем:\n"
                f"  ./venv/bin/python scripts/send_angel_announcement.py "
                f"--env-file {args.env_file} --group --topic-id {args.topic_id}"
            )
            return 0

        if args.group:
            group_id = int(cfg.CLUB_GROUP_ID or 0)
            topic_id = int(args.topic_id)
            if group_id == 0:
                logger.error("CLUB_GROUP_ID не задан в %s", args.env_file)
                return 1
            html = ap_txt.build_group_announcement_html(test=False)
            ok = await _send_to_group_topic(
                bot,
                chat_id=group_id,
                topic_id=topic_id,
                html=html,
                keyboard=keyboard,
            )
            if ok:
                logger.info("OK group=%s topic=%s", group_id, topic_id)
                print(f"Готово: анонс опубликован в группу {group_id}, топик {topic_id}.")
                return 0
            return 1

        logger.error("Укажите --admins, --group или --dry-run")
        return 1
    finally:
        await bot.session.close()
        await storage.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Разовый анонс «Стать ангелом» (запуск из dev, prod .env)."
    )
    parser.add_argument(
        "--env-file",
        default=DEFAULT_PROD_ENV,
        help=f"Путь к .env прода (по умолчанию {DEFAULT_PROD_ENV})",
    )
    parser.add_argument(
        "--admins",
        action="store_true",
        help="Тест: отправить админам в личку с префиксом [ТЕСТ]",
    )
    parser.add_argument(
        "--group",
        action="store_true",
        help="Опубликовать в клубную группу (CLUB_GROUP_ID из env)",
    )
    parser.add_argument(
        "--topic-id",
        type=int,
        default=DEFAULT_TOPIC_ID,
        help=f"message_thread_id топика (по умолчанию {DEFAULT_TOPIC_ID})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Показать текст и deeplink, ничего не отправлять",
    )
    args = parser.parse_args()
    if sum(bool(x) for x in (args.admins, args.group, args.dry_run)) != 1:
        parser.error("Ровно один из флагов: --admins, --group, --dry-run")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
