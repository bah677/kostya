#!/usr/bin/env python3
"""
Разовая отправка B-PAY / C-PAY после сбоя БД 2026-08-31.

Только 2 получателя с видимым сбоем оплаты (по prod-логам):
  B-PAY 1057286720 — biblia, YooKassa OK, запись в БД упала
  C-PAY 798750809  — club, тарифы не загрузились

Примеры (prod .env, без деплоя):

  cd /home/appuser/dev/kostya/club
  ./venv/bin/python scripts/send_outage_payment_recovery.py --dry-run
  ./venv/bin/python scripts/send_outage_payment_recovery.py
  ./venv/bin/python scripts/send_outage_payment_recovery.py --biblia-only
  ./venv/bin/python scripts/send_outage_payment_recovery.py --club-only
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger("send_outage_payment_recovery")

DEFAULT_BIBLIA_ENV = "/home/appuser/biblia/.env"
DEFAULT_CLUB_ENV = "/home/appuser/club/.env"

BIBLIA_UID = 1057286720
CLUB_UID = 798750809

BIBLIA_TEXT = (
    "Добрый день 🙏\n\n"
    "Утром у нас был технический сбой — вы начали поддержку проекта, "
    "платёж в системе создался, но из‑за ошибки базы мы не смогли его "
    "сохранить и провести до конца.\n\n"
    "Сейчас всё починено. Нажмите кнопку ниже — откроется оплата с начала, "
    "займёт пару минут.\n\n"
    "Спасибо, что поддерживаете проект 💛"
)

CLUB_TEXT = (
    "Здравствуйте!\n\n"
    "Сегодня утром при попытке оплатить участие в Клубе у нас был технический сбой — "
    "тарифы не загрузились из‑за недоступности базы данных.\n\n"
    "Сейчас всё восстановлено. Нажмите кнопку ниже — откроются тарифы, "
    "можно продолжить с того же места.\n\n"
    "Приносим извинения за неудобство 🙏"
)


def _biblia_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Продолжить оплату",
                    callback_data="payment_start",
                )
            ]
        ]
    )


def _club_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Оплата и тарифы",
                    callback_data="menu_act:payment",
                )
            ]
        ]
    )


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="B-PAY / C-PAY outage recovery DMs")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--biblia-only", action="store_true")
    p.add_argument("--club-only", action="store_true")
    p.add_argument("--biblia-env", default=DEFAULT_BIBLIA_ENV)
    p.add_argument("--club-env", default=DEFAULT_CLUB_ENV)
    return p.parse_args()


def _load_token(env_file: str, key: str) -> str:
    from dotenv import load_dotenv

    load_dotenv(env_file, override=True)
    token = (os.getenv(key) or "").strip()
    if not token:
        raise SystemExit(f"Missing {key} in {env_file}")
    return token


async def _send(
    bot: Bot,
    *,
    uid: int,
    text: str,
    keyboard: InlineKeyboardMarkup,
    dry_run: bool,
    label: str,
) -> bool:
    if dry_run:
        print(f"[dry-run] {label} uid={uid}")
        print(text)
        print(f"buttons: {keyboard.inline_keyboard}")
        return True
    try:
        await bot.send_message(
            chat_id=uid,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        logger.info("%s sent uid=%s", label, uid)
        print(f"OK {label} uid={uid}")
        return True
    except Exception as e:
        logger.error("%s failed uid=%s: %s", label, uid, e)
        print(f"FAIL {label} uid={uid}: {e}")
        return False


async def main() -> None:
    args = _parse()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    send_biblia = not args.club_only
    send_club = not args.biblia_only

    ok = True

    if send_biblia:
        token = _load_token(args.biblia_env, "BIBLIA_BOT_TOKEN")
        bot = Bot(token=token)
        try:
            ok &= await _send(
                bot,
                uid=BIBLIA_UID,
                text=BIBLIA_TEXT,
                keyboard=_biblia_keyboard(),
                dry_run=args.dry_run,
                label="B-PAY",
            )
        finally:
            await bot.session.close()

    if send_club:
        token = _load_token(args.club_env, "MIRON_BOT_TOKEN")
        bot = Bot(token=token)
        try:
            ok &= await _send(
                bot,
                uid=CLUB_UID,
                text=CLUB_TEXT,
                keyboard=_club_keyboard(),
                dry_run=args.dry_run,
                label="C-PAY",
            )
        finally:
            await bot.session.close()

    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
