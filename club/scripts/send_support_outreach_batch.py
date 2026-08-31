#!/usr/bin/env python3
"""Разовая отправка: Irina (wish #39) + ответ по TKT_CL32FA310B."""

from __future__ import annotations

import asyncio
import html
import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import load_dotenv

from bot.texts import ru_support as support_txt
from bot.texts import ru_user_menu as menu_txt
from bot.utils.admin_channel import admin_channel_chat_id, send_admin_html_message
from bot.utils.user_ui import CB_MAIN_MENU, with_main_menu
from config import load_config
from storage.user_storage import UserStorage

logger = logging.getLogger("send_support_outreach_batch")
DEFAULT_PROD_ENV = "/home/appuser/club/.env"
_MSK = ZoneInfo("Europe/Moscow")

CB_WISH_BOARD = "menu_act:wish_board"

IRINA_DONOR_ID = 512742212
IRINA_DONOR_TEXT = """Ирина, добрый день!

Спасибо, что написали в поддержку по просьбе о продлении подписки (тикет TKT_CL2FBFCE43).

Мы разобрались: при оплате бот ошибочно проверял, что у автора просьбы ещё активна подписка. Если участник уже вышел из клуба, оплата блокировалась — хотя просьбу он успел создать, пока был в клубе.

Мы это исправили. Ваша просьба (#39) по-прежнему закреплена за вами — в общий пул мы её не возвращали.

Что можно сделать сейчас:

1️⃣ Выполнить просьбу — если хотите помочь:
   /menu → Доска добрых дел → Мои отклики → «В работе» → открыть просьбу → «🎁 Подарить продление» и оплатить тариф.
   После оплаты подписка продлится автору просьбы, ей придёт сообщение со ссылкой вернуться в клуб.

2️⃣ Вернуть просьбу в общий пул — если сейчас не готовы:
   в той же карточке просьбы нажмите «↩️ Отказаться от просьбы» — её смогут взять другие участники.

Если при оплате снова что-то пойдёт не так — напишите, разберёмся. Спасибо, что помогаете на доске добрых дел 🙏"""

TICKET_NUMBER = "TKT_CL32FA310B"
TICKET_REPLY_BODY = """Ирина, добрый день!

Спасибо, что написали — понимаем, как важно для вас остаться в клубе, и что сейчас непросто с финансами.

В клубе есть «Доска добрых дел»: можно разместить просьбу о продлении подписки — другие участники иногда берут такие просьбы и оплачивают продление в подарок. Это не гарантия, но многим уже помогло.

Как разместить просьбу: нажмите кнопку ниже → «Попросить о помощи» → выберите «Продление подписки» и коротко опишите ситуацию. Просьба появится на доске — участники смогут откликнуться.

Если останутся вопросы — пишите, мы рядом 🙏"""


def _wish_board_keyboard() -> InlineKeyboardMarkup:
    return with_main_menu(
        [
            [
                InlineKeyboardButton(
                    text=menu_txt.BTN_WISH_BOARD,
                    callback_data=CB_WISH_BOARD,
                )
            ]
        ]
    )


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv(DEFAULT_PROD_ENV, override=True)
    cfg = load_config()
    token = (cfg.MIRON_BOT_TOKEN or "").strip()
    if not token:
        logger.error("MIRON_BOT_TOKEN не задан")
        return 1

    storage = UserStorage(cfg.database_url)
    await storage.initialize()
    bot = Bot(token=token)

    try:
        # 1. Irina — даритель wish #39 (не через тикет, т.к. закрыт)
        await bot.send_message(
            IRINA_DONOR_ID,
            IRINA_DONOR_TEXT,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        logger.info("✅ DM sent to donor Irina uid=%s", IRINA_DONOR_ID)

        # 2. Ответ по открытому тикету финансовых затруднений
        ticket = await storage.get_ticket_by_number(TICKET_NUMBER)
        if not ticket:
            logger.error("Ticket %s not found", TICKET_NUMBER)
            return 1
        if ticket.get("status") != "open":
            logger.error("Ticket %s status=%s — expected open", TICKET_NUMBER, ticket.get("status"))
            return 1

        user_id = int(ticket["user_id"])
        admin_id = int(cfg.SUPER_ADMIN_ID or 0) or 304631563

        row = await storage.apply_support_ticket_admin_reply(
            TICKET_NUMBER,
            TICKET_REPLY_BODY,
            admin_id,
        )
        if not row:
            logger.error("apply_support_ticket_admin_reply failed for %s", TICKET_NUMBER)
            return 1

        user_dm = support_txt.support_ticket_reply_html(
            ticket_number=TICKET_NUMBER,
            admin_response=TICKET_REPLY_BODY,
        )
        await bot.send_message(
            user_id,
            user_dm,
            parse_mode=ParseMode.HTML,
            reply_markup=_wish_board_keyboard(),
            disable_web_page_preview=True,
        )
        await storage.update_ticket_status(
            TICKET_NUMBER,
            "closed",
            admin_id=admin_id,
            admin_response=TICKET_REPLY_BODY,
        )
        logger.info("✅ Ticket reply sent uid=%s ticket=%s", user_id, TICKET_NUMBER)

        # 3. Обновление в админском чате
        ts = datetime.now(_MSK).strftime("%d.%m.%Y %H:%M")
        user_row = await storage.get_user(user_id) or {}
        uname = user_row.get("username")
        name = (user_row.get("first_name") or "Участник").strip()
        username_str = f"@{uname}" if uname else support_txt.NO_USERNAME
        admin_text = (
            f"✅ <b>Ответ поддержки отправлен</b>\n\n"
            f"🎫 <b>Тикет:</b> <code>{html.escape(TICKET_NUMBER)}</code>\n"
            f"👤 <b>Пользователь:</b> {html.escape(name)} ({html.escape(username_str)})\n"
            f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
            f"⏰ <b>Время:</b> {ts}\n\n"
            f"💬 <b>Исходное обращение:</b>\n"
            f"{html.escape(str(ticket.get('user_message') or '')[:500])}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ <b>Ответ поддержки</b> (скрипт outreach)\n\n"
            f"{html.escape(TICKET_REPLY_BODY)}"
        )
        thread_id = (
            cfg.SUPPORT_THREAD_ID if cfg.SUPPORT_THREAD_ID and cfg.SUPPORT_THREAD_ID > 0 else None
        )
        ok = await send_admin_html_message(bot, admin_text, thread_id=thread_id)
        if ok:
            logger.info("✅ Admin chat update sent for %s", TICKET_NUMBER)
        else:
            logger.error("❌ Admin chat update failed for %s", TICKET_NUMBER)
            return 1

        return 0
    finally:
        await bot.session.close()
        await storage.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
