"""ЭФ-3: запрос и раздача записи эфира."""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from aiogram.enums import ParseMode

from bot.services.club_engagement_policy import decide_club_outreach
from bot.utils.admin_channel import send_admin_html_message
from config import config
from storage.db.club_schedule import INVITEABLE_CONTENT_TYPES

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

_URL_RE = re.compile(r"(https?://\S+)", re.I)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=MSK)
    return dt.astimezone(MSK)


async def request_recordings_from_admins(user_storage, bot) -> int:
    now = datetime.now(MSK)
    events = await user_storage.list_events_needing_recording_request(now=now)
    n = 0
    topic_id = config.CLUB_SCHEDULE_ADMIN_TOPIC_ID
    for ev in events:
        title = html.escape((ev.get("title") or "Эфир").strip())
        eid = int(ev["id"])
        text = (
            f"🎙 Эфир «{title}» закончился.\n"
            f"Пришлите ссылку на запись — я разошлю тем, кто не был.\n\n"
            f"Ответьте в этот топик: <code>запись {eid} https://…</code>"
        )
        ok = await send_admin_html_message(
            bot,
            text,
            thread_id=topic_id if topic_id and topic_id > 0 else None,
        )
        if ok:
            await user_storage.mark_recording_requested(eid)
            await user_storage.log_interaction(
                user_id=0,
                event_category="air",
                event_type="air_recording_requested",
                data={"event_id": eid},
                source="air_recording",
                outcome="success",
            )
            n += 1
    return n


def parse_recording_admin_command(text: str) -> Optional[tuple[int, str]]:
    """`запись 123 https://…` или `запись https://…` (последний эфир без url)."""
    raw = (text or "").strip()
    m = re.match(
        r"(?i)^запись\s+(\d+)\s+(https?://\S+)",
        raw,
    )
    if m:
        return int(m.group(1)), m.group(2).rstrip(").,;")
    m2 = re.match(r"(?i)^запись\s+(https?://\S+)", raw)
    if m2:
        return 0, m2.group(1).rstrip(").,;")
    return None


async def apply_recording_url(
    user_storage,
    bot,
    *,
    event_id: int,
    url: str,
) -> Optional[Dict[str, Any]]:
    ev = await user_storage.get_club_schedule_event(event_id)
    if not ev:
        return None
    updated = await user_storage.set_schedule_recording_url(event_id, url)
    if not updated:
        return None

    ends = updated.get("ends_at") or updated.get("starts_at")
    hours_since = None
    if isinstance(ends, datetime):
        hours_since = round(
            (datetime.now(MSK) - _aware(ends)).total_seconds() / 3600.0, 1
        )

    await user_storage.log_interaction(
        user_id=0,
        event_category="air",
        event_type="air_recording_ready",
        data={"event_id": event_id, "hours_since_end": hours_since},
        source="air_recording",
        outcome="success",
    )

    title = html.escape((updated.get("title") or "Эфир").strip())
    safe_url = html.escape(url.strip())
    group_text = (
        f"📼 <b>Запись эфира</b>\n\n"
        f"«{title}»\n\n"
        f'<a href="{safe_url}">Смотреть запись</a>'
    )
    gid = int(config.CLUB_GROUP_ID or 0)
    if gid:
        try:
            await bot.send_message(
                gid,
                group_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
            )
        except Exception as e:
            logger.warning("publish recording to group: %s", e)

    await _dm_recording_to_absentees(user_storage, bot, updated, url)
    return updated


async def _user_in_air_window(
    user_storage, user_id: int, *, club_group_id: int, start: datetime, end: datetime
) -> bool:
    try:
        async with user_storage.get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT 1 FROM messages
                WHERE user_id = $1 AND chat_id = $2
                  AND role = 'user' AND deleted_at IS NULL
                  AND created_at >= $3 AND created_at <= $4
                LIMIT 1
                """,
                user_id,
                club_group_id,
                start,
                end,
            )
            return row is not None
    except Exception as e:
        logger.debug("user_in_air_window: %s", e)
        return False


async def _dm_recording_to_absentees(
    user_storage, bot, event: Dict[str, Any], url: str
) -> int:
    event_id = int(event["id"])
    starts = _aware(event["starts_at"])
    ends = event.get("ends_at")
    ends_a = _aware(ends) if isinstance(ends, datetime) else starts + timedelta(hours=2)
    window_start = starts - timedelta(minutes=30)
    window_end = ends_a + timedelta(minutes=30)
    gid = int(config.CLUB_GROUP_ID or 0)
    title = html.escape((event.get("title") or "Эфир").strip())
    safe_url = html.escape(url.strip())
    body = (
        f"📼 <b>Запись эфира</b>\n\n"
        f"«{title}»\n\n"
        f'<a href="{safe_url}">Открыть запись</a>'
    )
    sent = 0
    for uid in await user_storage.list_user_ids_with_active_license():
        if gid and await _user_in_air_window(
            user_storage, uid, club_group_id=gid, start=window_start, end=window_end
        ):
            continue

        decision = await decide_club_outreach(
            user_storage, uid, kind="air_recording", api_key=""
        )
        if not decision.allow:
            continue
        if not await user_storage.try_claim_air_send(
            event_id=event_id, user_id=uid, kind="recording"
        ):
            continue
        try:
            await bot.send_message(
                uid, body, parse_mode=ParseMode.HTML, disable_web_page_preview=False
            )
            sent += 1
            await user_storage.increment_proactive_sent_today(uid)
        except Exception as e:
            logger.debug("recording dm uid=%s: %s", uid, e)

    await user_storage.log_interaction(
        user_id=0,
        event_category="air",
        event_type="air_recording_sent",
        data={"event_id": event_id, "recipients": sent},
        source="air_recording",
        outcome="success",
    )
    return sent


async def resolve_latest_event_without_recording(user_storage) -> Optional[int]:
    now = datetime.now(MSK)
    events = await user_storage.list_club_schedule_events(
        from_at=now - timedelta(days=14),
        to_at=now,
        content_types=list(INVITEABLE_CONTENT_TYPES),
        limit=50,
    )
    for ev in reversed(events):
        if not (ev.get("recording_url") or "").strip():
            return int(ev["id"])
    return None
