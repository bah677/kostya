#!/usr/bin/env python3
"""Три рассылки молитвы (A/B/C): аудитории, planned на завтра 09:00 МСК, превью SUPER_ADMIN."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storage.mailing_storage import MailingStorage  # noqa: E402
from storage.user_storage import UserStorage  # noqa: E402

MSK = ZoneInfo("Europe/Moscow")
ENV_PATH = Path("/home/appuser/biblia/.env")
DRAFT_DIR = ROOT / "data" / "mail_drafts"

SEGMENTS = (
    {
        "key": "A",
        "name": "Молитва · открыл, но не написал",
        "text_file": DRAFT_DIR / "prayer_seg_a.txt",
        "users_file": DRAFT_DIR / "seg_a_uids.txt",
        "button": "🙏 Помолиться",
        "sql": """
WITH touched AS (
  SELECT DISTINCT user_id FROM interaction_logs WHERE event_type='prayer_start'
  UNION
  SELECT DISTINCT user_id FROM token_usage WHERE request_kind LIKE 'personal_prayer%'
),
intake AS (
  SELECT DISTINCT user_id FROM token_usage
  WHERE request_kind='personal_prayer_intake'
),
act AS (
  SELECT DISTINCT user_id FROM messages
  WHERE sender_type='user' AND chat_type='private'
    AND created_at > NOW() - interval '60 days'
)
SELECT t.user_id FROM touched t
JOIN act USING (user_id)
WHERE t.user_id NOT IN (SELECT user_id FROM intake)
ORDER BY 1
""",
    },
    {
        "key": "B",
        "name": "Молитва · ровно одна",
        "text_file": DRAFT_DIR / "prayer_seg_b.txt",
        "users_file": DRAFT_DIR / "seg_b_uids.txt",
        "button": "🙏 Помолиться",
        "sql": """
WITH comp AS (
  SELECT user_id, count(*) n FROM token_usage
  WHERE request_kind LIKE 'personal_prayer_compose%'
  GROUP BY 1
),
act AS (
  SELECT DISTINCT user_id FROM messages
  WHERE sender_type='user' AND chat_type='private'
    AND created_at > NOW() - interval '60 days'
)
SELECT c.user_id FROM comp c
JOIN act USING (user_id)
WHERE c.n = 1
ORDER BY 1
""",
    },
    {
        "key": "C",
        "name": "Молитва · ни разу не пробовал",
        "text_file": DRAFT_DIR / "prayer_seg_c.txt",
        "users_file": DRAFT_DIR / "seg_c_uids.txt",
        "button": "🙏 Помолиться",
        "sql": """
WITH touched AS (
  SELECT DISTINCT user_id FROM interaction_logs WHERE event_type='prayer_start'
  UNION
  SELECT DISTINCT user_id FROM token_usage WHERE request_kind LIKE 'personal_prayer%'
),
act AS (
  SELECT DISTINCT user_id FROM messages
  WHERE sender_type='user' AND chat_type='private'
    AND created_at > NOW() - interval '60 days'
)
SELECT a.user_id FROM act a
WHERE a.user_id NOT IN (SELECT user_id FROM touched)
ORDER BY 1
""",
    },
)


def _load_env() -> dict:
    vals = dotenv_values(ENV_PATH)
    # dotenv_values не ставит в os.environ — делаем сами
    for k, v in vals.items():
        if v is not None and k not in os.environ:
            os.environ[k] = v
    # override critical from file
    for k in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD",
              "BIBLIA_BOT_TOKEN", "SUPER_ADMIN_ID"):
        if vals.get(k):
            os.environ[k] = vals[k]
    return vals


def _tomorrow_9_msk() -> datetime:
    now = datetime.now(MSK)
    day = (now + timedelta(days=1)).date()
    return datetime(day.year, day.month, day.day, 9, 0, 0, tzinfo=MSK)


async def main() -> None:
    _load_env()
    token = (os.getenv("BIBLIA_BOT_TOKEN") or "").strip()
    super_id = int(os.getenv("SUPER_ADMIN_ID") or 0)
    if not token or super_id <= 0:
        raise SystemExit("BIBLIA_BOT_TOKEN / SUPER_ADMIN_ID")

    db_url = (
        f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
        f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}"
        f"/{os.getenv('DB_NAME')}"
    )
    storage = UserStorage(db_url)
    await storage.initialize()
    mstore = MailingStorage(storage)
    DRAFT_DIR.mkdir(parents=True, exist_ok=True)

    scheduled_at = _tomorrow_9_msk()
    scheduled_utc = scheduled_at.astimezone()

    admin_ids: list[int] = [super_id]
    try:
        for r in await storage.list_telegram_admin_ids() or []:
            tid = int(r.get("telegram_user_id") or 0)
            if tid > 0:
                admin_ids.append(tid)
    except Exception as e:
        print(f"WARN admins: {e}", file=sys.stderr)
    admin_ids = sorted(set(admin_ids))

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    created: list[tuple[str, int, int, str]] = []

    try:
        async with storage.get_connection() as conn:
            for seg in SEGMENTS:
                rows = await conn.fetch(seg["sql"])
                uids = [int(r["user_id"]) for r in rows]
                seg["users_file"].write_text(
                    "\n".join(str(u) for u in uids) + "\n", encoding="utf-8"
                )
                base_n = len(uids)
                all_uids = sorted(set(uids) | set(admin_ids))
                text = seg["text_file"].read_text(encoding="utf-8").strip()
                n_chars = len(text)
                buttons = [
                    {
                        "text": seg["button"],
                        "style": "success",
                        "callback": "prayer_start",
                    }
                ]
                cid = await mstore.create_campaign(
                    {
                        "name": f"{seg['name']} · {scheduled_at.strftime('%Y-%m-%d')}",
                        "text": text,
                        "parse_mode": "HTML",
                        "scheduled_at": scheduled_utc,
                        "has_ref_link": False,
                        "buttons": buttons,
                        "created_by": super_id,
                        "media_type": None,
                        "media_file_id": None,
                        "attachments": None,
                    }
                )
                if not cid:
                    raise SystemExit(f"create_campaign failed seg={seg['key']}")
                added = await mstore.add_audience_batch(int(cid), all_uids)
                created.append((seg["key"], int(cid), added, text))
                print(
                    f"OK {seg['key']} cid={cid} base={base_n} audience={added} "
                    f"chars={n_chars}"
                )

                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="❌ Отменить рассылку",
                                callback_data=f"mdraft_no_{cid}",
                            ),
                        ]
                    ]
                )
                preview = (
                    f"📧 <b>Черновик {seg['key']}</b> <code>{cid}</code>\n"
                    f"<b>{seg['name']}</b>\n"
                    f"Аудитория: <b>{added}</b> (база {base_n} + админы)\n"
                    f"Кнопка: <b>{seg['button']}</b> → <code>prayer_start</code>\n"
                    f"Знаков: {n_chars}\n"
                    f"⏰ <b>Уже planned:</b> "
                    f"{scheduled_at.strftime('%d.%m.%Y %H:%M')} МСК\n"
                    f"<i>Не жмите «Запустить сейчас» — уйдёт по расписанию. "
                    f"Если текст не ок — «Отменить».</i>\n\n"
                    f"——— текст ———\n\n{text}"
                )
                await bot.send_message(super_id, preview, reply_markup=kb)

        summary = (
            "📋 <b>Три рассылки молитвы — на согласование</b>\n"
            f"Старт: <b>{scheduled_at.strftime('%d.%m.%Y в %H:%M')} МСК</b>\n"
            f"Марафон: не активен.\n\n"
            + "\n".join(
                f"· <b>{k}</b> id=<code>{cid}</code> · {n} чел."
                for k, cid, n, _ in created
            )
            + "\n\nСегменты не пересекаются. D (2+) не рассылаем — ведёт DEV-4."
        )
        await bot.send_message(super_id, summary)
        print("preview sent to", super_id)
    finally:
        await bot.session.close()
        await storage.close()


if __name__ == "__main__":
    asyncio.run(main())
