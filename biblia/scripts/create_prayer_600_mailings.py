#!/usr/bin/env python3
"""
600 молитв · когорты T1–T7 без пересечений.

- Марафон T8 (цель 30 000 ₽ = 600×50)
- Бонус ×3 озвучки двум из T1
- Planned-кампании на 20.09.2026 09:00 МСК
- Превью + размеры когорт → SUPER_ADMIN в личку

Запуск (пишет в prod БД biblia_bot — рассылки идут оттуда):

  cd /home/appuser/dev/kostya/biblia
  ./venv/bin/python scripts/create_prayer_600_mailings.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
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

SCHEDULE_AT = datetime(2026, 9, 20, 9, 0, 0, tzinfo=MSK)
OUTAGE_SINCE = datetime(2026, 9, 12, 0, 0, 0, tzinfo=MSK)
MARATHON_GOAL_RUB = 30_000.0  # 600 × 50 ₽

# Приоритет дедупа: T1 > T2 > T3 > T4 > T5 > T6 > T7
SEGMENTS = (
    {
        "key": "T1",
        "name": "600 · заплатили во время сбоя",
        "desc": "Заплатили 500₽ 15.09 / 300₽ 19.09 и упёрлись в сбой озвучки. "
        "По ТЗ — личное; здесь рассылка на согласование. Бонус ×3 уже выдан.",
        "text_file": DRAFT_DIR / "prayer_600_t1.txt",
        "buttons": [
            {"text": "🙏 Заказать молитву", "style": "success", "callback": "prayer_start"},
        ],
        "sql": """
WITH avail AS (
  SELECT user_id FROM users
  WHERE is_active IS TRUE AND COALESCE(mailing_consent, TRUE) IS TRUE
)
SELECT DISTINCT p.user_id
  FROM payments p
  JOIN prayer_tech_incidents i
    ON i.user_id = p.user_id
   AND i.kind = 'voice_tts_failed'
   AND i.created_at >= $1::timestamptz
  JOIN avail a ON a.user_id = p.user_id
 WHERE p.status = 'succeeded'
   AND (
     (p.created_at::date = '2026-09-15' AND p.amount_rub = 500)
     OR (p.created_at::date = '2026-09-19' AND p.amount_rub = 300)
   )
 ORDER BY 1
""",
        "params": (OUTAGE_SINCE,),
    },
    {
        "key": "T2",
        "name": "600 · крупные жертвователи",
        "desc": "Сумма донатов ≥ 5 000 ₽, топ-10, без пересечения с T1. "
        "По ТЗ — личное от Константина; здесь рассылка на согласование.",
        "text_file": DRAFT_DIR / "prayer_600_t2.txt",
        "buttons": [
            {
                "text": "🙏 Открыть молитвы для других",
                "style": "primary",
                "callback": "marathon_open",
            },
        ],
        "sql": """
WITH avail AS (
  SELECT user_id FROM users
  WHERE is_active IS TRUE AND COALESCE(mailing_consent, TRUE) IS TRUE
),
t1 AS (
  SELECT DISTINCT p.user_id
    FROM payments p
    JOIN prayer_tech_incidents i
      ON i.user_id = p.user_id
     AND i.kind = 'voice_tts_failed'
     AND i.created_at >= $1::timestamptz
    JOIN avail a ON a.user_id = p.user_id
   WHERE p.status = 'succeeded'
     AND (
       (p.created_at::date = '2026-09-15' AND p.amount_rub = 500)
       OR (p.created_at::date = '2026-09-19' AND p.amount_rub = 300)
     )
),
ranked AS (
  SELECT p.user_id, sum(p.amount_rub) AS tot
    FROM payments p
    JOIN avail a ON a.user_id = p.user_id
   WHERE p.status = 'succeeded' AND COALESCE(p.amount_rub, 0) > 0
   GROUP BY 1
  HAVING sum(p.amount_rub) >= 5000
)
SELECT user_id FROM ranked
 WHERE user_id NOT IN (SELECT user_id FROM t1)
 ORDER BY tot DESC
 LIMIT 10
""",
        "params": (OUTAGE_SINCE,),
    },
    {
        "key": "T3",
        "name": "600 · упёрлись в сбой озвучки",
        "desc": "voice_tts_failed с 12.09, активные с согласием на рассылку; без T1/T2.",
        "text_file": DRAFT_DIR / "prayer_600_t3.txt",
        "buttons": [
            {"text": "🙏 Заказать молитву", "style": "success", "callback": "prayer_start"},
        ],
        "sql": """
WITH avail AS (
  SELECT user_id FROM users
  WHERE is_active IS TRUE AND COALESCE(mailing_consent, TRUE) IS TRUE
),
t1 AS (
  SELECT DISTINCT p.user_id
    FROM payments p
    JOIN prayer_tech_incidents i
      ON i.user_id = p.user_id
     AND i.kind = 'voice_tts_failed'
     AND i.created_at >= $1::timestamptz
    JOIN avail a ON a.user_id = p.user_id
   WHERE p.status = 'succeeded'
     AND (
       (p.created_at::date = '2026-09-15' AND p.amount_rub = 500)
       OR (p.created_at::date = '2026-09-19' AND p.amount_rub = 300)
     )
),
t2 AS (
  SELECT user_id FROM (
    SELECT p.user_id, sum(p.amount_rub) AS tot
      FROM payments p JOIN avail a ON a.user_id = p.user_id
     WHERE p.status = 'succeeded' AND COALESCE(p.amount_rub, 0) > 0
     GROUP BY 1 HAVING sum(p.amount_rub) >= 5000
     ORDER BY tot DESC
  ) x
   WHERE user_id NOT IN (SELECT user_id FROM t1)
   LIMIT 10
)
SELECT DISTINCT i.user_id
  FROM prayer_tech_incidents i
  JOIN avail a ON a.user_id = i.user_id
 WHERE i.kind = 'voice_tts_failed'
   AND i.created_at >= $1::timestamptz
   AND i.user_id NOT IN (SELECT user_id FROM t1)
   AND i.user_id NOT IN (SELECT user_id FROM t2)
 ORDER BY 1
""",
        "params": (OUTAGE_SINCE,),
    },
    {
        "key": "T4",
        "name": "600 · упёрлись в лимит",
        "desc": "Получали текст «лимит бесплатных голосовых…» за 21 день; без T1–T3.",
        "text_file": DRAFT_DIR / "prayer_600_t4.txt",
        "buttons": [
            {
                "text": "🙏 Открыть молитвы для других",
                "style": "primary",
                "callback": "marathon_open",
            },
            {"text": "🙏 Заказать молитву себе", "style": "success", "callback": "prayer_start"},
        ],
        "sql": """
WITH avail AS (
  SELECT user_id FROM users
  WHERE is_active IS TRUE AND COALESCE(mailing_consent, TRUE) IS TRUE
),
t1 AS (
  SELECT DISTINCT p.user_id
    FROM payments p
    JOIN prayer_tech_incidents i
      ON i.user_id = p.user_id
     AND i.kind = 'voice_tts_failed'
     AND i.created_at >= $1::timestamptz
    JOIN avail a ON a.user_id = p.user_id
   WHERE p.status = 'succeeded'
     AND (
       (p.created_at::date = '2026-09-15' AND p.amount_rub = 500)
       OR (p.created_at::date = '2026-09-19' AND p.amount_rub = 300)
     )
),
t2 AS (
  SELECT user_id FROM (
    SELECT p.user_id, sum(p.amount_rub) AS tot
      FROM payments p JOIN avail a ON a.user_id = p.user_id
     WHERE p.status = 'succeeded' AND COALESCE(p.amount_rub, 0) > 0
     GROUP BY 1 HAVING sum(p.amount_rub) >= 5000
     ORDER BY tot DESC
  ) x
   WHERE user_id NOT IN (SELECT user_id FROM t1)
   LIMIT 10
),
t3 AS (
  SELECT DISTINCT i.user_id
    FROM prayer_tech_incidents i JOIN avail a ON a.user_id = i.user_id
   WHERE i.kind = 'voice_tts_failed' AND i.created_at >= $1::timestamptz
     AND i.user_id NOT IN (SELECT user_id FROM t1)
     AND i.user_id NOT IN (SELECT user_id FROM t2)
)
SELECT DISTINCT m.user_id
  FROM messages m
  JOIN avail a ON a.user_id = m.user_id
 WHERE m.sender_type = 'bot' AND m.chat_type = 'private'
   AND m.content ILIKE '%лимит бесплатных голосовых%'
   AND m.created_at >= NOW() - interval '21 days'
   AND m.user_id NOT IN (SELECT user_id FROM t1)
   AND m.user_id NOT IN (SELECT user_id FROM t2)
   AND m.user_id NOT IN (SELECT user_id FROM t3)
 ORDER BY 1
""",
        "params": (OUTAGE_SINCE,),
    },
    {
        "key": "T5",
        "name": "600 · донатили за 60 дней",
        "desc": "Успешный платёж за 60 дней; без T1–T4.",
        "text_file": DRAFT_DIR / "prayer_600_t5.txt",
        "buttons": [
            {
                "text": "🙏 Открыть молитвы для других",
                "style": "primary",
                "callback": "marathon_open",
            },
        ],
        "sql": """
WITH avail AS (
  SELECT user_id FROM users
  WHERE is_active IS TRUE AND COALESCE(mailing_consent, TRUE) IS TRUE
),
t1 AS (
  SELECT DISTINCT p.user_id
    FROM payments p
    JOIN prayer_tech_incidents i
      ON i.user_id = p.user_id
     AND i.kind = 'voice_tts_failed'
     AND i.created_at >= $1::timestamptz
    JOIN avail a ON a.user_id = p.user_id
   WHERE p.status = 'succeeded'
     AND (
       (p.created_at::date = '2026-09-15' AND p.amount_rub = 500)
       OR (p.created_at::date = '2026-09-19' AND p.amount_rub = 300)
     )
),
t2 AS (
  SELECT user_id FROM (
    SELECT p.user_id, sum(p.amount_rub) AS tot
      FROM payments p JOIN avail a ON a.user_id = p.user_id
     WHERE p.status = 'succeeded' AND COALESCE(p.amount_rub, 0) > 0
     GROUP BY 1 HAVING sum(p.amount_rub) >= 5000
     ORDER BY tot DESC
  ) x
   WHERE user_id NOT IN (SELECT user_id FROM t1)
   LIMIT 10
),
t3 AS (
  SELECT DISTINCT i.user_id
    FROM prayer_tech_incidents i JOIN avail a ON a.user_id = i.user_id
   WHERE i.kind = 'voice_tts_failed' AND i.created_at >= $1::timestamptz
     AND i.user_id NOT IN (SELECT user_id FROM t1)
     AND i.user_id NOT IN (SELECT user_id FROM t2)
),
t4 AS (
  SELECT DISTINCT m.user_id
    FROM messages m JOIN avail a ON a.user_id = m.user_id
   WHERE m.sender_type = 'bot' AND m.chat_type = 'private'
     AND m.content ILIKE '%лимит бесплатных голосовых%'
     AND m.created_at >= NOW() - interval '21 days'
     AND m.user_id NOT IN (SELECT user_id FROM t1)
     AND m.user_id NOT IN (SELECT user_id FROM t2)
     AND m.user_id NOT IN (SELECT user_id FROM t3)
)
SELECT DISTINCT p.user_id
  FROM payments p
  JOIN avail a ON a.user_id = p.user_id
 WHERE p.status = 'succeeded'
   AND p.created_at >= NOW() - interval '60 days'
   AND p.user_id NOT IN (SELECT user_id FROM t1)
   AND p.user_id NOT IN (SELECT user_id FROM t2)
   AND p.user_id NOT IN (SELECT user_id FROM t3)
   AND p.user_id NOT IN (SELECT user_id FROM t4)
 ORDER BY 1
""",
        "params": (OUTAGE_SINCE,),
    },
    {
        "key": "T6",
        "name": "600 · донатили давно",
        "desc": "Последний донат старше 60 дней; без T1–T5.",
        "text_file": DRAFT_DIR / "prayer_600_t6.txt",
        "buttons": [
            {
                "text": "🙏 Открыть молитвы для других",
                "style": "primary",
                "callback": "marathon_open",
            },
            {"text": "🙏 Заказать молитву себе", "style": "success", "callback": "prayer_start"},
        ],
        "sql": """
WITH avail AS (
  SELECT user_id FROM users
  WHERE is_active IS TRUE AND COALESCE(mailing_consent, TRUE) IS TRUE
),
t1 AS (
  SELECT DISTINCT p.user_id
    FROM payments p
    JOIN prayer_tech_incidents i
      ON i.user_id = p.user_id
     AND i.kind = 'voice_tts_failed'
     AND i.created_at >= $1::timestamptz
    JOIN avail a ON a.user_id = p.user_id
   WHERE p.status = 'succeeded'
     AND (
       (p.created_at::date = '2026-09-15' AND p.amount_rub = 500)
       OR (p.created_at::date = '2026-09-19' AND p.amount_rub = 300)
     )
),
t2 AS (
  SELECT user_id FROM (
    SELECT p.user_id, sum(p.amount_rub) AS tot
      FROM payments p JOIN avail a ON a.user_id = p.user_id
     WHERE p.status = 'succeeded' AND COALESCE(p.amount_rub, 0) > 0
     GROUP BY 1 HAVING sum(p.amount_rub) >= 5000
     ORDER BY tot DESC
  ) x
   WHERE user_id NOT IN (SELECT user_id FROM t1)
   LIMIT 10
),
t3 AS (
  SELECT DISTINCT i.user_id
    FROM prayer_tech_incidents i JOIN avail a ON a.user_id = i.user_id
   WHERE i.kind = 'voice_tts_failed' AND i.created_at >= $1::timestamptz
     AND i.user_id NOT IN (SELECT user_id FROM t1)
     AND i.user_id NOT IN (SELECT user_id FROM t2)
),
t4 AS (
  SELECT DISTINCT m.user_id
    FROM messages m JOIN avail a ON a.user_id = m.user_id
   WHERE m.sender_type = 'bot' AND m.chat_type = 'private'
     AND m.content ILIKE '%лимит бесплатных голосовых%'
     AND m.created_at >= NOW() - interval '21 days'
     AND m.user_id NOT IN (SELECT user_id FROM t1)
     AND m.user_id NOT IN (SELECT user_id FROM t2)
     AND m.user_id NOT IN (SELECT user_id FROM t3)
),
t5 AS (
  SELECT DISTINCT p.user_id
    FROM payments p JOIN avail a ON a.user_id = p.user_id
   WHERE p.status = 'succeeded' AND p.created_at >= NOW() - interval '60 days'
     AND p.user_id NOT IN (SELECT user_id FROM t1)
     AND p.user_id NOT IN (SELECT user_id FROM t2)
     AND p.user_id NOT IN (SELECT user_id FROM t3)
     AND p.user_id NOT IN (SELECT user_id FROM t4)
)
SELECT e.user_id
  FROM (
    SELECT user_id, max(created_at) AS last_p
      FROM payments WHERE status = 'succeeded'
     GROUP BY 1
  ) e
  JOIN avail a ON a.user_id = e.user_id
 WHERE e.last_p < NOW() - interval '60 days'
   AND e.user_id NOT IN (SELECT user_id FROM t1)
   AND e.user_id NOT IN (SELECT user_id FROM t2)
   AND e.user_id NOT IN (SELECT user_id FROM t3)
   AND e.user_id NOT IN (SELECT user_id FROM t4)
   AND e.user_id NOT IN (SELECT user_id FROM t5)
 ORDER BY 1
""",
        "params": (OUTAGE_SINCE,),
    },
    {
        "key": "T7",
        "name": "600 · молились, не донатили",
        "desc": "personal_prayer_intake за 30 дней, никогда не донатили; без T1–T6.",
        "text_file": DRAFT_DIR / "prayer_600_t7.txt",
        "buttons": [
            {
                "text": "🙏 Передать дальше",
                "style": "primary",
                "callback": "marathon_open",
            },
            {"text": "🙏 Заказать молитву себе", "style": "success", "callback": "prayer_start"},
        ],
        "sql": """
WITH avail AS (
  SELECT user_id FROM users
  WHERE is_active IS TRUE AND COALESCE(mailing_consent, TRUE) IS TRUE
),
t1 AS (
  SELECT DISTINCT p.user_id
    FROM payments p
    JOIN prayer_tech_incidents i
      ON i.user_id = p.user_id
     AND i.kind = 'voice_tts_failed'
     AND i.created_at >= $1::timestamptz
    JOIN avail a ON a.user_id = p.user_id
   WHERE p.status = 'succeeded'
     AND (
       (p.created_at::date = '2026-09-15' AND p.amount_rub = 500)
       OR (p.created_at::date = '2026-09-19' AND p.amount_rub = 300)
     )
),
t2 AS (
  SELECT user_id FROM (
    SELECT p.user_id, sum(p.amount_rub) AS tot
      FROM payments p JOIN avail a ON a.user_id = p.user_id
     WHERE p.status = 'succeeded' AND COALESCE(p.amount_rub, 0) > 0
     GROUP BY 1 HAVING sum(p.amount_rub) >= 5000
     ORDER BY tot DESC
  ) x
   WHERE user_id NOT IN (SELECT user_id FROM t1)
   LIMIT 10
),
t3 AS (
  SELECT DISTINCT i.user_id
    FROM prayer_tech_incidents i JOIN avail a ON a.user_id = i.user_id
   WHERE i.kind = 'voice_tts_failed' AND i.created_at >= $1::timestamptz
     AND i.user_id NOT IN (SELECT user_id FROM t1)
     AND i.user_id NOT IN (SELECT user_id FROM t2)
),
t4 AS (
  SELECT DISTINCT m.user_id
    FROM messages m JOIN avail a ON a.user_id = m.user_id
   WHERE m.sender_type = 'bot' AND m.chat_type = 'private'
     AND m.content ILIKE '%лимит бесплатных голосовых%'
     AND m.created_at >= NOW() - interval '21 days'
     AND m.user_id NOT IN (SELECT user_id FROM t1)
     AND m.user_id NOT IN (SELECT user_id FROM t2)
     AND m.user_id NOT IN (SELECT user_id FROM t3)
),
t5 AS (
  SELECT DISTINCT p.user_id
    FROM payments p JOIN avail a ON a.user_id = p.user_id
   WHERE p.status = 'succeeded' AND p.created_at >= NOW() - interval '60 days'
     AND p.user_id NOT IN (SELECT user_id FROM t1)
     AND p.user_id NOT IN (SELECT user_id FROM t2)
     AND p.user_id NOT IN (SELECT user_id FROM t3)
     AND p.user_id NOT IN (SELECT user_id FROM t4)
),
t6 AS (
  SELECT e.user_id
    FROM (
      SELECT user_id, max(created_at) AS last_p
        FROM payments WHERE status = 'succeeded' GROUP BY 1
    ) e
    JOIN avail a ON a.user_id = e.user_id
   WHERE e.last_p < NOW() - interval '60 days'
     AND e.user_id NOT IN (SELECT user_id FROM t1)
     AND e.user_id NOT IN (SELECT user_id FROM t2)
     AND e.user_id NOT IN (SELECT user_id FROM t3)
     AND e.user_id NOT IN (SELECT user_id FROM t4)
     AND e.user_id NOT IN (SELECT user_id FROM t5)
),
prior AS (
  SELECT user_id FROM t1 UNION SELECT user_id FROM t2 UNION SELECT user_id FROM t3
  UNION SELECT user_id FROM t4 UNION SELECT user_id FROM t5 UNION SELECT user_id FROM t6
)
SELECT DISTINCT t.user_id
  FROM token_usage t
  JOIN avail a ON a.user_id = t.user_id
 WHERE t.request_kind = 'personal_prayer_intake'
   AND t.created_at >= NOW() - interval '30 days'
   AND NOT EXISTS (
     SELECT 1 FROM payments p
      WHERE p.user_id = t.user_id AND p.status = 'succeeded'
   )
   AND t.user_id NOT IN (SELECT user_id FROM prior)
 ORDER BY 1
""",
        "params": (OUTAGE_SINCE,),
    },
)


def _load_env() -> None:
    vals = dotenv_values(ENV_PATH)
    for k, v in vals.items():
        if v is not None and k not in os.environ:
            os.environ[k] = v
    for k in (
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
        "BIBLIA_BOT_TOKEN",
        "SUPER_ADMIN_ID",
    ):
        if vals.get(k):
            os.environ[k] = vals[k]


async def _ensure_marathon(storage: UserStorage, super_id: int) -> dict:
    active = await storage.get_active_donation_marathon()
    if active:
        print(f"marathon already active id={active['id']} name={active.get('name')}")
        return active
    desc = (DRAFT_DIR / "prayer_600_t8_marathon.html").read_text(encoding="utf-8").strip()
    row = await storage.create_donation_marathon(
        name="🕯️ 600 молитв",
        description_html=desc,
        goal_amount=MARATHON_GOAL_RUB,
        goal_currency="RUB",
        accept_rub=True,
        accept_usd=True,
        accept_crypto=True,
        created_by=super_id,
    )
    if not row:
        raise SystemExit("create_donation_marathon failed")
    print(f"marathon created id={row['id']}")
    return row


async def _grant_t1_bonus(storage: UserStorage, uids: list[int]) -> None:
    for uid in uids:
        ok = await storage.grant_prayer_voice_bonus(uid, 3)
        print(f"T1 bonus×3 uid={uid} ok={ok}")


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
    try:
        await storage.ensure_prayer_voice_quota_schema()
    except Exception as e:
        print(f"WARN quota schema: {e}", file=sys.stderr)
    mstore = MailingStorage(storage)
    DRAFT_DIR.mkdir(parents=True, exist_ok=True)

    marathon = await _ensure_marathon(storage, super_id)
    scheduled_utc = SCHEDULE_AT.astimezone(timezone.utc)

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    created: list[tuple[str, int, int, str]] = []
    seen: set[int] = set()

    try:
        async with storage.get_connection() as conn:
            for seg in SEGMENTS:
                rows = await conn.fetch(seg["sql"], *seg["params"])
                uids = [int(r["user_id"]) for r in rows]
                overlap = sorted(set(uids) & seen)
                if overlap:
                    raise SystemExit(
                        f"overlap in {seg['key']}: {overlap[:10]}… ({len(overlap)})"
                    )
                seen.update(uids)

                if seg["key"] == "T1":
                    await _grant_t1_bonus(storage, uids)

                uid_file = DRAFT_DIR / f"prayer_600_{seg['key'].lower()}_uids.txt"
                uid_file.write_text(
                    "\n".join(str(u) for u in uids) + ("\n" if uids else ""),
                    encoding="utf-8",
                )
                text = seg["text_file"].read_text(encoding="utf-8").strip()
                cid = await mstore.create_campaign(
                    {
                        "name": f"{seg['name']} · {SCHEDULE_AT.strftime('%Y-%m-%d')}",
                        "text": text,
                        "parse_mode": "HTML",
                        "scheduled_at": scheduled_utc,
                        "has_ref_link": False,
                        "buttons": seg["buttons"],
                        "created_by": super_id,
                        "media_type": None,
                        "media_file_id": None,
                        "attachments": None,
                    }
                )
                if not cid:
                    raise SystemExit(f"create_campaign failed {seg['key']}")
                added = await mstore.add_audience_batch(int(cid), uids) if uids else 0
                created.append((seg["key"], int(cid), added, text))
                print(
                    f"OK {seg['key']} cid={cid} n={added} chars={len(text)}"
                )

                btn_lines = "\n".join(
                    f"· {b['text']} → <code>{b['callback']}</code>"
                    for b in seg["buttons"]
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
                    f"{seg['desc']}\n\n"
                    f"Размер когорты: <b>{added}</b>\n"
                    f"Кнопки:\n{btn_lines}\n"
                    f"⏰ <b>planned:</b> {SCHEDULE_AT.strftime('%d.%m.%Y %H:%M')} МСК\n"
                    f"<i>Уйдёт по расписанию. Если не ок — «Отменить».</i>\n\n"
                    f"——— текст ———\n\n{text}"
                )
                await bot.send_message(super_id, preview, reply_markup=kb)
                await asyncio.sleep(0.35)

        total = sum(n for _, _, n, _ in created)
        summary = (
            "📋 <b>600 молитв — на согласование</b>\n"
            f"Старт всех волн: <b>{SCHEDULE_AT.strftime('%d.%m.%Y в %H:%M')} МСК</b>\n"
            f"Марафон: id=<code>{marathon['id']}</code> «{marathon.get('name')}» "
            f"цель {int(MARATHON_GOAL_RUB)} ₽\n"
            f"Всего уникальных получателей: <b>{total}</b> "
            f"(пересечений нет)\n\n"
            + "\n".join(
                f"· <b>{k}</b> id=<code>{cid}</code> · {n} чел."
                for k, cid, n, _ in created
            )
            + "\n\n"
            "<b>T9</b> — приписка к «Слову из Писания» (08:00 МСК) уже в коде "
            "на 20.09 (нужен деплой biblia до 8:00).\n"
            "<b>Голос Константина</b> в T3/T4 не добавлял — только после "
            "подтверждения, что основной синтез оплачен.\n"
            "<b>T1</b>: бонус ×3 озвучки выдан; текст обещает это."
        )
        await bot.send_message(super_id, summary)
        print("preview sent to", super_id, "total", total)
    finally:
        await bot.session.close()
        await storage.close()


if __name__ == "__main__":
    asyncio.run(main())
