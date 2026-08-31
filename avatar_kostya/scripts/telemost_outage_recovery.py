#!/usr/bin/env python3
"""
После сбоя Postgres/диска: отбивка в RAG-группе + карточки писем Телемоста за N дней.

  cd /home/appuser/dev/kostya/avatar_kostya
  .venv/bin/python scripts/telemost_outage_recovery.py --days 5
  .venv/bin/python scripts/telemost_outage_recovery.py --days 5 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from html import escape as html_escape

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot
from aiogram.enums import ParseMode
from dotenv import load_dotenv

from bot.integrations.rag_bridge import try_build_rag_stack
from bot.utils.rag_admin_context import rag_admin_chat_topic
from config import load_app_config, load_biblia_bot_config
from storage.user_storage import UserStorage
from telemost_mail.service import TelemostMailService

logger = logging.getLogger("telemost_outage_recovery")

ANNOUNCEMENT_HTML = (
    "🛠 <b>Техсбой починен</b> (31.08.2026)\n\n"
    "С <b>29–31 августа</b> Postgres и диск были недоступны — обработка писем "
    "Телемоста и RAG остановились. Включили заново.\n\n"
    "Ниже — <b>реальные конспекты</b> за последние дни, по которым нужно решение: "
    "«Загрузить в RAG» или «Игнорировать». Дубликаты из спама во время сбоя можно "
    "просто пролистать.\n\n"
    "Если карточки не пришли — напишите <code>/telemost_poll</code> в личку боту."
)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
    cfg = load_app_config()
    bc = load_biblia_bot_config()

    chat_id, topic_id = rag_admin_chat_topic()
    if not chat_id:
        logger.error("RAG_ADMIN_CHAT_ID не задан")
        return 1

    token = (bc.BIBLIA_BOT_TOKEN or cfg.MIRON_BOT_TOKEN or "").strip()
    if not token:
        logger.error("MIRON_BOT_TOKEN не задан")
        return 1

    storage = UserStorage(cfg.database_url)
    await storage.initialize()

    bot = Bot(token=token)
    try:
        if args.dry_run:
            logger.info("[dry-run] announcement → chat=%s topic=%s", chat_id, topic_id)
        else:
            await bot.send_message(
                chat_id,
                ANNOUNCEMENT_HTML,
                parse_mode=ParseMode.HTML,
                message_thread_id=topic_id,
            )
            logger.info("✅ announcement sent")

        rs = try_build_rag_stack(cfg)

        svc = TelemostMailService.from_config(
            cfg,
            user_storage=storage,
            material_index=rs.materials if rs else None,
        )
        if not svc.enabled:
            logger.error("TelemostMailService не настроен (IMAP / RAG / chat / speakers)")
            return 1

        from bot.features.telemost_mail_sync import TelemostMailFeature

        tm = TelemostMailFeature()
        tm.set_bot(type("App", (), {"bot": bot, "user_storage": storage})())

        async def notify_cb(note: dict) -> None:
            if args.dry_run:
                logger.info(
                    "[dry-run] offer pending=%s subject=%r",
                    note.get("pending_id"),
                    (note.get("subject") or "")[:80],
                )
                return
            await tm.notify_pending_note(bot, note)

        if args.dry_run:
            logger.info("[dry-run] backfill_mail days=%s", args.days)
            return 0

        stats = await svc.backfill_mail(
            args.days,
            notify_cb=notify_cb,
            decision_timeout_sec=0,
        )
        summary = (
            f"📬 <b>Догрузка Телемоста</b> ({args.days} дн.)\n\n"
            f"Просмотрено: <b>{stats.scanned}</b>\n"
            f"Карточек отправлено: <b>{stats.offered}</b>\n"
            f"Пропущено (кэш): <b>{stats.skipped_cached}</b>\n"
            f"Ошибок: <b>{stats.errors}</b>"
        )
        if stats.messages:
            summary += "\n\n<i>" + html_escape("; ".join(stats.messages[:5])) + "</i>"
        await bot.send_message(
            chat_id,
            summary,
            parse_mode=ParseMode.HTML,
            message_thread_id=topic_id,
        )
        logger.info(
            "backfill done scanned=%s offered=%s errors=%s",
            stats.scanned,
            stats.offered,
            stats.errors,
        )
        return 0
    finally:
        await bot.session.close()
        await storage.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
