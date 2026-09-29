"""Рассылка и RSVP закрытой встречи встречающих."""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime
from typing import Iterable, List, Optional, Set
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.texts import ru_greeter_meeting as txt
from config import config

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

MEETING_KEY = "2026-09-30-kostya"
MEETING_AT = datetime(2026, 9, 30, 13, 0, tzinfo=MSK)
MORNING_AT = datetime(2026, 9, 30, 9, 0, tzinfo=MSK)
PRE_MEETING_AT = datetime(2026, 9, 30, 12, 45, tzinfo=MSK)

# Нет активной подписки / не в клубе — не слать напоминания
EXCLUDE_USER_IDS = {
    1608138917,  # Оксана @oksana_arinushkin
    1915441022,  # Анжела Сиврова @angel_biscuit
    1336205081,  # Sabina Alizade — бот заблокирован, без подписки
}

CB_COMING = f"gm:{MEETING_KEY}:coming"
CB_CANT = f"gm:{MEETING_KEY}:cant"

PAUSE_SEC = 0.35


def rsvp_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=txt.BTN_COMING, callback_data=CB_COMING)],
            [InlineKeyboardButton(text=txt.BTN_CANT, callback_data=CB_CANT)],
        ]
    )


async def collect_meeting_recipients(user_storage) -> List[int]:
    """Активные встречающие + все админы (без дублей и исключений)."""
    ids: Set[int] = set()
    for uid in await user_storage.list_active_club_greeter_ids():
        if uid > 0:
            ids.add(int(uid))
    for row in await user_storage.list_telegram_admin_ids():
        uid = int(row["telegram_user_id"])
        if uid > 0:
            ids.add(uid)
    if config.SUPER_ADMIN_ID:
        ids.add(int(config.SUPER_ADMIN_ID))
    ids -= EXCLUDE_USER_IDS
    return sorted(ids)


async def send_meeting_dm(
    bot: Bot,
    *,
    user_id: int,
    html_text: str,
    with_buttons: bool,
) -> bool:
    kwargs = {
        "chat_id": user_id,
        "text": html_text,
        "parse_mode": ParseMode.HTML,
        "disable_web_page_preview": False,
    }
    if with_buttons:
        kwargs["reply_markup"] = rsvp_keyboard()
    try:
        await bot.send_message(**kwargs)
        return True
    except Exception as e:
        logger.error("greeter meeting DM uid=%s: %s", user_id, e)
        return False


async def blast_invite(
    bot: Bot,
    user_storage,
    *,
    recipients: Optional[Iterable[int]] = None,
    pause_sec: float = PAUSE_SEC,
) -> dict:
    uids = list(recipients) if recipients is not None else await collect_meeting_recipients(
        user_storage
    )
    ok = fail = 0
    for uid in uids:
        if await send_meeting_dm(
            bot, user_id=uid, html_text=txt.INVITE_HTML, with_buttons=True
        ):
            ok += 1
        else:
            fail += 1
        await asyncio.sleep(pause_sec)
    return {"ok": ok, "fail": fail, "total": len(uids)}


async def blast_wave(
    bot: Bot,
    user_storage,
    *,
    kind: str,
    pause_sec: float = PAUSE_SEC,
) -> dict:
    """kind: morning | pre — разный текст по статусу RSVP."""
    uids = await collect_meeting_recipients(user_storage)
    ok = fail = 0
    for uid in uids:
        row = await user_storage.get_greeter_meeting_rsvp(
            meeting_key=MEETING_KEY, user_id=uid
        )
        response = (row or {}).get("response")
        if response == "coming":
            body = txt.MORNING_COMING_HTML if kind == "morning" else txt.PRE_COMING_HTML
            with_buttons = False
        elif response == "cant":
            body = txt.MORNING_CANT_HTML if kind == "morning" else txt.PRE_CANT_HTML
            with_buttons = False
        else:
            body = (
                txt.MORNING_NEED_RSVP_HTML
                if kind == "morning"
                else txt.PRE_NEED_RSVP_HTML
            )
            with_buttons = True
        if await send_meeting_dm(
            bot, user_id=uid, html_text=body, with_buttons=with_buttons
        ):
            ok += 1
        else:
            fail += 1
        await asyncio.sleep(pause_sec)
    return {"ok": ok, "fail": fail, "total": len(uids), "kind": kind}


def _person_label(row: dict) -> str:
    uname = (row.get("username") or "").strip()
    fname = (row.get("first_name") or "").strip()
    if uname:
        return f"@{html.escape(uname)}"
    if fname:
        return html.escape(fname)
    return f"<code>{row.get('user_id')}</code>"


async def format_rsvp_report_html(user_storage) -> str:
    rows = await user_storage.list_greeter_meeting_rsvp(meeting_key=MEETING_KEY)
    coming = [r for r in rows if r.get("response") == "coming"]
    cant = [r for r in rows if r.get("response") == "cant"]
    recipients = await collect_meeting_recipients(user_storage)
    responded = {int(r["user_id"]) for r in rows}
    silent = [uid for uid in recipients if uid not in responded]

    def _lines(items: list) -> str:
        if not items:
            return "—"
        return "\n".join(
            f"• {_person_label(r)} <code>{r['user_id']}</code>" for r in items
        )

    silent_lines = (
        "\n".join(f"• <code>{uid}</code>" for uid in silent) if silent else "—"
    )
    return (
        f"📋 <b>RSVP встреча {MEETING_KEY}</b>\n\n"
        f"✅ Придут ({len(coming)}):\n{_lines(coming)}\n\n"
        f"🙏 Не могут ({len(cant)}):\n{_lines(cant)}\n\n"
        f"⏳ Без ответа ({len(silent)} / {len(recipients)}):\n{silent_lines}"
    )

