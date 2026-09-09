"""Сервис встречающих: назначение, перенос, follow-up бота."""

from __future__ import annotations

import html as html_mod
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional
from zoneinfo import ZoneInfo

from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.texts import ru_gift_wave as txt
from bot.utils.club_welcome import welcome_question_for_user
from config import config

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")


def club_message_deep_link(
    *,
    chat_id: int,
    message_id: int,
    thread_id: Optional[int] = None,
) -> str:
    """Ссылка на сообщение в супергруппе (t.me/c/...)."""
    raw = str(chat_id)
    if raw.startswith("-100"):
        internal = raw[4:]
    else:
        internal = raw.lstrip("-")
    if thread_id and int(thread_id) > 0:
        return f"https://t.me/c/{internal}/{int(thread_id)}/{int(message_id)}"
    return f"https://t.me/c/{internal}/{int(message_id)}"


def _greeter_keyboard(link: str, assignment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=txt.BTN_GREETER_OPEN, url=link)],
            [
                InlineKeyboardButton(
                    text=txt.BTN_GREETER_PAUSE_TODAY,
                    callback_data=f"gw:pause:{assignment_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=txt.BTN_GREETER_LEAVE,
                    callback_data=f"gw:leave:{assignment_id}",
                )
            ],
        ]
    )


async def assign_greeter_for_newcomer(
    *,
    user_storage,
    bot: "Bot",
    newcomer_id: int,
    newcomer_name: str,
    about_text: str,
    message_id: int,
    thread_id: Optional[int] = None,
    exclude_greeter_ids: Optional[list[int]] = None,
    attempt: int = 1,
) -> Optional[int]:
    greeter_id = await user_storage.pick_club_greeter(
        exclude_ids=exclude_greeter_ids or []
    )
    if not greeter_id:
        logger.warning("greeter: no available greeter for uid=%s", newcomer_id)
        return None

    aid = await user_storage.create_greeter_assignment(
        greeter_id=greeter_id,
        newcomer_id=newcomer_id,
        newcomer_msg_id=message_id,
        attempt=attempt,
    )
    if not aid:
        return None

    link = club_message_deep_link(
        chat_id=int(config.CLUB_GROUP_ID),
        message_id=message_id,
        thread_id=thread_id,
    )
    about = (about_text or "").strip() or txt.GREETER_ABOUT_EMPTY
    about = html_mod.escape(about[:500])
    name = html_mod.escape(newcomer_name or f"id{newcomer_id}")
    body = (
        txt.GREETER_REASSIGN_HTML if attempt > 1 else txt.GREETER_ASSIGN_HTML
    ).format(name=name, about=about, link=link)

    try:
        await bot.send_message(
            greeter_id,
            body,
            parse_mode=ParseMode.HTML,
            reply_markup=_greeter_keyboard(link, aid),
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error("greeter DM failed gid=%s: %s", greeter_id, e)

    try:
        await user_storage.log_interaction(
            user_id=newcomer_id,
            event_category="gift_wave",
            event_type="greeter_assigned",
            data={
                "greeter_id": greeter_id,
                "assignment_id": aid,
                "attempt": attempt,
            },
            source="club_greeter",
            outcome="success",
        )
    except Exception:
        pass
    return aid


async def process_greeter_timeouts(*, user_storage, bot: "Bot") -> None:
    """90 мин → reassign; 180 мин (attempt>=2 без ответа) → bot follow-up."""
    pending_90 = await user_storage.list_pending_greeter_assignments(
        older_than_minutes=90
    )
    for row in pending_90:
        attempt = int(row.get("attempt") or 1)
        aid = int(row["id"])
        newcomer_id = int(row["newcomer_id"])
        greeter_id = int(row["greeter_id"])
        msg_id = int(row["newcomer_msg_id"] or 0)
        age_min = (
            datetime.now(MSK) - row["assigned_at"].astimezone(MSK)
        ).total_seconds() / 60.0

        if attempt == 1 and age_min >= 90:
            await user_storage.mark_greeter_reassigned(aid)
            try:
                await user_storage.log_interaction(
                    user_id=newcomer_id,
                    event_category="gift_wave",
                    event_type="greeter_reassigned",
                    data={"from_greeter": greeter_id, "assignment_id": aid},
                    source="club_greeter",
                    outcome="success",
                )
            except Exception:
                pass
            user = await user_storage.get_user(newcomer_id)
            name = " ".join(
                p
                for p in (
                    (user or {}).get("first_name") or "",
                    (user or {}).get("last_name") or "",
                )
                if p
            ).strip() or f"id{newcomer_id}"
            about = ""
            if msg_id:
                # текст возьмём из messages если есть
                try:
                    async with user_storage.pool.acquire() as conn:
                        m = await conn.fetchrow(
                            """
                            SELECT content FROM messages
                            WHERE user_id = $1
                              AND telegram_message_id = $2
                            ORDER BY created_at DESC
                            LIMIT 1
                            """,
                            newcomer_id,
                            msg_id,
                        )
                        if m:
                            about = (m["content"] or "")[:500]
                except Exception:
                    pass
            await assign_greeter_for_newcomer(
                user_storage=user_storage,
                bot=bot,
                newcomer_id=newcomer_id,
                newcomer_name=name,
                about_text=about,
                message_id=msg_id or 1,
                exclude_greeter_ids=[greeter_id],
                attempt=2,
            )
            continue

        if attempt >= 2 and age_min >= 90:
            # суммарно ~180 мин с первого назначения
            await user_storage.mark_greeter_bot_followup(aid)
            await _bot_followup_in_group(
                user_storage=user_storage,
                bot=bot,
                newcomer_id=newcomer_id,
                reply_to_message_id=msg_id or None,
            )


async def _bot_followup_in_group(
    *,
    user_storage,
    bot: "Bot",
    newcomer_id: int,
    reply_to_message_id: Optional[int],
) -> None:
    user = await user_storage.get_user(newcomer_id)
    if user and user.get("username"):
        mention = f"@{html_mod.escape(user['username'])}"
    else:
        name = html_mod.escape(
            ((user or {}).get("first_name") or f"друг")
        )
        mention = f'<a href="tg://user?id={newcomer_id}">{name}</a>'
    q = welcome_question_for_user(newcomer_id, offset=1)
    text = txt.GREETER_BOT_FOLLOWUP_HTML.format(mention=mention, question=q)
    kwargs = dict(
        chat_id=int(config.CLUB_GROUP_ID),
        text=text,
        parse_mode=ParseMode.HTML,
    )
    tid = int(getattr(config, "WELCOME_TOPIC_ID", 0) or 0)
    if tid:
        kwargs["message_thread_id"] = tid
    if reply_to_message_id:
        kwargs["reply_to_message_id"] = reply_to_message_id
    try:
        await bot.send_message(**kwargs)
    except Exception as e:
        logger.error("bot followup failed uid=%s: %s", newcomer_id, e)


async def pause_greeter_for_today(user_storage, user_id: int) -> None:
    now = datetime.now(MSK)
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    await user_storage.pause_greeter_until(user_id, tomorrow)


def tomorrow_msk_midnight() -> datetime:
    now = datetime.now(MSK)
    return (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
