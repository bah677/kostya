#!/usr/bin/env python3
"""
Создать черновик рассылки (planned, scheduled через 7 дней) + превью SUPER_ADMIN в личку.
Кнопки: mdraft_ok_<id> / mdraft_no_<id> (обработчик в admin_mailing).

В аудиторию всегда добавляются SUPER_ADMIN_ID и все из таблицы admins (контроль).

Пример:
  cd /home/appuser/dev/kostya/biblia
  ./venv/bin/python scripts/create_mailing_draft_preview.py \\
    --env /home/appuser/biblia/.env \\
    --name "Сбой ответов 2026-07-24" \\
    --users-file /tmp/biblia_ds_users.txt \\
    --text-file /tmp/outage_mail.txt \\
    --button-text "🙏 Попробовать ещё раз" \\
    --button-callback prayer_start
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storage.mailing_storage import MailingStorage  # noqa: E402
from storage.user_storage import UserStorage  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--env", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--users-file", required=True)
    p.add_argument("--text-file", required=True)
    p.add_argument("--token-env", default="BIBLIA_BOT_TOKEN",
                   help="Имя переменной с токеном бота")
    p.add_argument(
        "--button-text",
        default="",
        help="Текст inline-кнопки у получателя (вместе с --button-callback)",
    )
    p.add_argument(
        "--button-callback",
        default="",
        help="callback_data кнопки, напр. prayer_start",
    )
    return p.parse_args()


async def main() -> None:
    args = _parse_args()
    # override=True: иначе локальный/шелл env или .env в cwd может указать на другую БД
    load_dotenv(args.env, override=True)

    token = (os.getenv(args.token_env) or os.getenv("BOT_TOKEN") or "").strip()
    if not token:
        raise SystemExit(f"Нет токена ({args.token_env} / BOT_TOKEN) в {args.env}")

    super_id = int(os.getenv("SUPER_ADMIN_ID") or 0)
    if super_id <= 0:
        raise SystemExit("SUPER_ADMIN_ID не задан")

    text = Path(args.text_file).read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit("Пустой текст")

    uids: list[int] = []
    for line in Path(args.users_file).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.isdigit():
            uids.append(int(line))
    base_n = len(set(uids))
    if base_n == 0:
        raise SystemExit("Пустой список получателей")

    db_url = (
        f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
        f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}"
        f"/{os.getenv('DB_NAME') or os.getenv('BIBLIA_DB_NAME')}"
    )
    storage = UserStorage(db_url)
    await storage.initialize()
    mstore = MailingStorage(storage)

    # Контроль: всегда добавляем SUPER_ADMIN + всех из таблицы admins
    admin_ids: list[int] = []
    try:
        rows = await storage.list_telegram_admin_ids()
        for r in rows or []:
            tid = int(r.get("telegram_user_id") or 0)
            if tid > 0:
                admin_ids.append(tid)
    except Exception as e:
        print(f"WARN list_telegram_admin_ids: {e}", file=sys.stderr)
    if super_id > 0:
        admin_ids.append(super_id)
    admin_ids = sorted(set(admin_ids))
    uids = sorted(set(uids) | set(admin_ids))

    buttons: list[dict] = []
    btn_text = (args.button_text or "").strip()
    btn_cb = (args.button_callback or "").strip()
    if btn_text and btn_cb:
        buttons = [{"text": btn_text, "style": "success", "callback": btn_cb}]

    scheduled_at = datetime.now(timezone.utc) + timedelta(days=7)
    campaign_row = {
        "name": args.name,
        "text": text,
        "parse_mode": "HTML",
        "scheduled_at": scheduled_at,
        "has_ref_link": False,
        "buttons": buttons,
        "created_by": super_id,
        "media_type": None,
        "media_file_id": None,
        "attachments": None,
    }
    cid = await mstore.create_campaign(campaign_row)
    if not cid:
        await storage.close()
        raise SystemExit("create_campaign failed")

    added = await mstore.add_audience_batch(int(cid), uids)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Запустить рассылку",
                    callback_data=f"mdraft_ok_{cid}",
                ),
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=f"mdraft_no_{cid}",
                ),
            ]
        ]
    )
    btn_line = ""
    if buttons:
        b0 = buttons[0]
        btn_line = (
            f"Кнопка у юзера: <b>{b0['text']}</b> → <code>{b0['callback']}</code>\n"
        )
    preview = (
        f"📧 <b>Черновик рассылки</b> <code>{cid}</code>\n"
        f"Имя: <code>{args.name}</code>\n"
        f"Получателей в аудитории: <b>{added}</b> "
        f"(база {base_n} + админы {len(admin_ids)})\n"
        f"{btn_line}"
        f"Отправка: <i>сразу после «Запустить»</i>\n\n"
        f"——— текст ——-\n\n"
        f"{text}"
    )

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await bot.send_message(super_id, preview, reply_markup=kb)
        print(f"OK campaign_id={cid} audience={added} preview→{super_id}")
    finally:
        await bot.session.close()
        await storage.close()


if __name__ == "__main__":
    asyncio.run(main())
