"""Рассылка Т2 (Библия, когорты Б1–Б5): команда в клубе → черновик в БиблияБоте."""

from __future__ import annotations

import html
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Set
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.services.biblia_club_campaign_report import (
    biblia_db_configured,
    create_biblia_pool,
)
from bot.services.gift_application_mailing import list_admin_recipient_ids
from bot.texts import ru_gift_application as txt
from config import config
from storage.db.gift_application import CAMPAIGN_ID

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

BIBLIA_COHORT_ORDER = ("B1", "B2", "B3", "B4", "B5")
BIBLIA_COHORT_ALL = ("TEST",) + BIBLIA_COHORT_ORDER
PORTION_SIZE = 300

BIBLIA_COHORT_TITLES = {
    "TEST": "TEST — только админы клуба (превью)",
    "B1": "Б1 — доноры Библии, активны ≤60 дн.",
    "B2": "Б2 — активны ≤60 дн., много дней/молитв",
    "B3": "Б3 — активны ≤60 дн., остальные",
    "B4": "Б4 — молчали 61–180 дн.",
    "B5": "Б5 — давно не писали (>180 дн.)",
}


def _club_bot_username() -> str:
    return (getattr(config, "TELEGRAM_BOT_USERNAME", None) or "Talk_God_Bot").lstrip(
        "@"
    )


def _gift_bib_url() -> str:
    return f"https://t.me/{_club_bot_username()}?start=gift_bib"


def _t2_buttons() -> List[Dict[str, str]]:
    return [
        {
            "text": txt.BTN_BIB_APPLY,
            "url": _gift_bib_url(),
            "style": "success",
        }
    ]


def _t2_body_html() -> str:
    """Т2 для HTML-рассылки Библии."""
    raw = (txt.T2_PLAIN or "").strip()
    # plain → простые абзацы; спецсимволы экранируем
    parts = [html.escape(p.strip()) for p in raw.split("\n\n") if p.strip()]
    return "\n\n".join(parts)


def _biblia_database_url() -> str:
    return (
        f"postgresql://{config.BIBLIA_DB_USER}:{config.BIBLIA_DB_PASSWORD}"
        f"@{config.BIBLIA_DB_HOST}:{int(config.BIBLIA_DB_PORT or 5432)}"
        f"/{config.BIBLIA_DB_NAME}"
    )


def _load_biblia_bot_token() -> str:
    """Токен БиблияБота: env или соседний biblia/.env (в клубе обычно нет)."""
    token = (os.getenv("BIBLIA_BOT_TOKEN") or "").strip()
    if token:
        return token
    for path in (
        "/home/appuser/biblia/.env",
        "/home/appuser/dev/kostya/biblia/.env",
    ):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    if k.strip() != "BIBLIA_BOT_TOKEN":
                        continue
                    val = v.strip().strip('"').strip("'")
                    if val:
                        return val
        except OSError:
            continue
    return ""


async def release_biblia_gift_mailing_reservation(
    club_storage, *, campaign_id: int
) -> int:
    """Отмена черновика Библии → вернуть user_id в пул (gift_mailing_sent)."""
    if not biblia_db_configured(config):
        return 0
    import asyncpg

    conn = await asyncpg.connect(_biblia_database_url())
    try:
        camp = await conn.fetchrow(
            "SELECT name FROM mailing_campaigns WHERE id = $1", int(campaign_id)
        )
        if not camp:
            return 0
        name = str(camp["name"] or "")
        if not name.startswith("gift-2026-09"):
            return 0
        rows = await conn.fetch(
            "SELECT user_id FROM mailing_audience WHERE campaign_id = $1",
            int(campaign_id),
        )
        uids = [int(r["user_id"]) for r in rows]
    finally:
        await conn.close()

    if not uids:
        return 0
    try:
        async with club_storage.get_connection() as cconn:
            result = await cconn.execute(
                """
                DELETE FROM gift_mailing_sent
                WHERE campaign = $1
                  AND user_id = ANY($2::bigint[])
                  AND cohort <> 'TEST'
                """,
                CAMPAIGN_ID,
                uids,
            )
            try:
                return int(result.split()[-1])
            except Exception:
                return 0
    except Exception as e:
        logger.warning(
            "release_biblia_gift_mailing_reservation #%s: %s", campaign_id, e
        )
        return 0


def _merge_with_admins(user_ids: Sequence[int], admin_ids: Sequence[int]) -> List[int]:
    seen: Set[int] = set()
    out: List[int] = []
    for uid in list(admin_ids) + list(user_ids):
        uid = int(uid)
        if uid <= 0 or uid in seen:
            continue
        seen.add(uid)
        out.append(uid)
    return out


async def _classify_biblia_user_ids(
    *,
    club_storage,
    biblia_pool,
    cohort: str,
    limit: int,
) -> List[int]:
    """Те же правила, что export_gift_biblia_cohorts.py."""
    c = cohort.upper()
    async with biblia_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH act AS (
              SELECT user_id,
                     count(DISTINCT created_at::date)
                       FILTER (WHERE created_at > NOW() - interval '60 days') AS d60,
                     max(created_at) AS last
              FROM messages WHERE sender_type = 'user' GROUP BY 1
            ),
            don AS (SELECT DISTINCT user_id FROM payments WHERE status = 'succeeded'),
            pr  AS (
              SELECT user_id, count(*) AS n FROM token_usage
              WHERE request_kind LIKE 'personal_prayer_compose%' GROUP BY 1
            )
            SELECT u.user_id, COALESCE(a.d60, 0) AS d60, a.last,
                   (d.user_id IS NOT NULL) AS donor, COALESCE(pr.n, 0) AS prayers
            FROM users u
            LEFT JOIN act a ON a.user_id = u.user_id
            LEFT JOIN don d ON d.user_id = u.user_id
            LEFT JOIN pr ON pr.user_id = u.user_id
            WHERE COALESCE(u.is_active, TRUE)
              AND u.bot_blocked_at IS NULL
              AND NOT COALESCE(u.is_banned, FALSE)
              AND COALESCE(u.mailing_consent, TRUE)
            """
        )

    async with club_storage.get_connection() as conn:
        excluded = await conn.fetch(
            """
            SELECT user_id FROM payments WHERE status = 'succeeded'
            UNION
            SELECT user_id FROM license
            UNION
            SELECT user_id FROM gift_mailing_sent WHERE campaign = $1
            UNION
            SELECT user_id FROM gift_application WHERE campaign = $1
            """,
            CAMPAIGN_ID,
        )
    excl = {int(r["user_id"]) for r in excluded}

    now = datetime.now(MSK)
    out: List[int] = []
    for r in rows:
        uid = int(r["user_id"])
        if uid in excl:
            continue
        last = r["last"]
        d60 = int(r["d60"] or 0)
        donor = bool(r["donor"])
        prayers = int(r["prayers"] or 0)
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=MSK)
        days_ago = (now - last.astimezone(MSK)).days if last else 10_000

        bucket = None
        if donor and days_ago <= 60:
            bucket = "B1"
        elif days_ago <= 60 and (d60 >= 4 or prayers >= 2):
            bucket = "B2"
        elif days_ago <= 60:
            bucket = "B3"
        elif 61 <= days_ago <= 180:
            bucket = "B4"
        else:
            bucket = "B5"

        if bucket == c:
            out.append(uid)
            if limit > 0 and len(out) >= limit:
                break
    return out


async def create_biblia_portion_draft(
    *,
    club_storage,
    admin_id: int,
    cohort: Optional[str] = None,
    limit: int = PORTION_SIZE,
) -> Dict[str, Any]:
    """Команда клуба → planned mailing_campaigns в БД Библии + mdraft в личку БиблияБотом."""
    if not biblia_db_configured(config):
        return {"ok": False, "reason": "biblia_db_not_configured"}

    token = _load_biblia_bot_token()
    if not token:
        return {"ok": False, "reason": "no_biblia_bot_token"}

    c = (cohort or "").strip().upper() or None
    if c and c not in BIBLIA_COHORT_ALL:
        return {
            "ok": False,
            "reason": "bad_cohort",
            "allowed": list(BIBLIA_COHORT_ALL),
        }

    order = list(BIBLIA_COHORT_ORDER)
    if c:
        order = [c]

    admin_ids = await list_admin_recipient_ids(club_storage)
    used_cohort: Optional[str] = None
    cohort_ids: List[int] = []

    biblia_pool = await create_biblia_pool(config)
    try:
        for name in order:
            if name == "TEST":
                cohort_ids = []
                used_cohort = "TEST"
                break
            cohort_ids = await _classify_biblia_user_ids(
                club_storage=club_storage,
                biblia_pool=biblia_pool,
                cohort=name,
                limit=limit,
            )
            if cohort_ids or name == c:
                used_cohort = name
                break
    finally:
        await biblia_pool.close()

    if used_cohort is None:
        return {"ok": False, "reason": "empty_cohort"}

    recipients = _merge_with_admins(cohort_ids, admin_ids)
    if not recipients:
        return {"ok": False, "reason": "no_recipients", "cohort": used_cohort}

    body = _t2_body_html()
    buttons = _t2_buttons()
    scheduled_at = datetime.now(timezone.utc) + timedelta(days=7)
    title = (
        f"gift-2026-09 Т2 {used_cohort} "
        f"{datetime.now(MSK).strftime('%m-%d %H:%M')}"
    )

    # Пишем кампанию в БД Библии (тот же schema, что у biblia MailingStorage).
    import json

    import asyncpg

    db_url = _biblia_database_url()
    conn = await asyncpg.connect(db_url)
    try:
        cid = await conn.fetchval(
            """
            INSERT INTO mailing_campaigns (
                name, text, parse_mode, scheduled_at, has_ref_link,
                media_type, media_file_id, created_by, buttons,
                campaign_source, attachments
            ) VALUES (
                $1, $2, 'HTML', $3, FALSE,
                NULL, NULL, $4, $5::jsonb,
                'manual', NULL
            )
            RETURNING id
            """,
            title,
            body,
            scheduled_at.replace(tzinfo=None),
            int(admin_id),
            json.dumps(buttons),
        )
        if not cid:
            return {"ok": False, "reason": "create_failed"}
        for i in range(0, len(recipients), 500):
            chunk = recipients[i : i + 500]
            await conn.executemany(
                """
                INSERT INTO mailing_audience (campaign_id, user_id)
                VALUES ($1, $2)
                ON CONFLICT (campaign_id, user_id) DO NOTHING
                """,
                [(int(cid), int(u)) for u in chunk],
            )
        added = int(
            await conn.fetchval(
                "SELECT COUNT(*)::int FROM mailing_audience WHERE campaign_id = $1",
                int(cid),
            )
            or 0
        )
    finally:
        await conn.close()

    if used_cohort != "TEST":
        for uid in cohort_ids:
            try:
                await club_storage.record_gift_mailing_sent(
                    uid, cohort=used_cohort, stage=2, delivery="sent"
                )
            except Exception:
                pass

    try:
        await club_storage.update_gift_campaign_state(club_cohort=used_cohort)
    except Exception:
        pass

    cohort_title = BIBLIA_COHORT_TITLES.get(used_cohort, used_cohort)
    preview_meta = (
        f"📧 <b>Черновик рассылки Библии · подарочная волна</b>\n"
        f"Кампания: <code>{cid}</code>\n"
        f"Когорта: <b>{html.escape(used_cohort)}</b> — {html.escape(cohort_title)}\n"
        f"Получателей: <b>{added}</b>"
        f" (когорта {len(cohort_ids)} + админы {len(admin_ids)})\n"
        f"Кнопка: {html.escape(txt.BTN_BIB_APPLY)} → "
        f"<code>{html.escape(_gift_bib_url())}</code>\n"
        f"Запуск — кнопкой ниже (воркер БиблияБота).\n\n"
        f"——— текст получателям ——-\n\n"
        f"{body}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=txt.BTN_BIB_APPLY,
                    url=_gift_bib_url(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Запустить рассылку",
                    callback_data=f"mdraft_ok_{cid}",
                ),
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=f"mdraft_no_{cid}",
                ),
            ],
        ]
    )

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        await bot.send_message(
            admin_id,
            preview_meta,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error("biblia gift draft preview uid=%s: %s", admin_id, e)
        await bot.session.close()
        return {
            "ok": False,
            "reason": "preview_failed",
            "campaign_id": int(cid),
            "error": str(e),
        }
    await bot.session.close()

    return {
        "ok": True,
        "campaign_id": int(cid),
        "cohort": used_cohort,
        "cohort_title": cohort_title,
        "recipients": len(recipients),
        "cohort_n": len(cohort_ids),
        "admins_n": len(admin_ids),
        "added": added,
    }
