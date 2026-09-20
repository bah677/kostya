"""ЭФ-3: запись эфира из поста в закрытой группе → личка тем, кто не был.

Админ не присылает ссылку вручную: выкладываете запись в топики
записи закрытой группы (см. CLUB_AIR_RECORDING_TOPIC_IDS) —
бот берёт ссылку на этот пост и рассылает отсутствовавшим.
Команда «запись <id> https://…» в топике расписания остаётся запасным путём.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from aiogram.enums import ParseMode
from aiogram.types import Message

from bot.services.club_engagement_policy import decide_club_outreach
from bot.services.club_greeter_service import club_message_deep_link
from config import config
from storage.db.club_schedule import INVITEABLE_CONTENT_TYPES

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

_RECORDING_HINT_RE = re.compile(
    r"(?i)(запис[ьи]|recording|повтор\w*\s+эфир|эфир\w*\s+запис)",
)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=MSK)
    return dt.astimezone(MSK)


def _recording_topic_ids() -> frozenset[int]:
    ids = getattr(config, "CLUB_AIR_RECORDING_TOPIC_IDS", None) or (3, 109)
    return frozenset(int(x) for x in ids if int(x) > 0)


def _looks_like_recording_post(message: Message) -> bool:
    """Пост в топике записи похож на выложенную запись эфира."""
    if message.video or message.video_note:
        return True
    if message.document:
        mime = (message.document.mime_type or "").lower()
        name = (message.document.file_name or "").lower()
        if mime.startswith("video/") or name.endswith(
            (".mp4", ".mov", ".mkv", ".webm", ".avi")
        ):
            return True
    text = (message.text or message.caption or "").strip()
    if not text:
        return False
    if _RECORDING_HINT_RE.search(text):
        return True
    return False


async def request_recordings_from_admins(user_storage, bot) -> int:
    """Больше не просим админов прислать URL — ждём пост в топиках записи.

    Оставлено как no-op для совместимости со старым cron-job.
    """
    return 0


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


async def maybe_capture_recording_from_group_message(
    user_storage,
    bot,
    message: Message,
) -> Optional[Dict[str, Any]]:
    """Если в топике записи выложили пост после эфира — ссылка + рассылка."""
    gid = int(config.CLUB_GROUP_ID or 0)
    if not gid or int(message.chat.id) != gid:
        return None
    if message.from_user and message.from_user.is_bot:
        return None

    allowed = _recording_topic_ids()
    thread_id = getattr(message, "message_thread_id", None)
    tid = int(thread_id) if thread_id else 0
    if tid not in allowed:
        return None

    if not _looks_like_recording_post(message):
        return None

    event_id = await resolve_latest_event_without_recording(user_storage)
    if not event_id:
        logger.info(
            "recording topic msg=%s thread=%s: нет эфира без записи — пропуск",
            message.message_id,
            tid,
        )
        return None

    url = club_message_deep_link(
        chat_id=int(message.chat.id),
        message_id=int(message.message_id),
        thread_id=tid,
    )
    updated = await apply_recording_url(
        user_storage,
        bot,
        event_id=event_id,
        url=url,
        publish_to_group=False,
    )
    if updated:
        logger.info(
            "air recording captured from topic=%s msg=%s → event=%s url=%s",
            tid,
            message.message_id,
            event_id,
            url,
        )
    return updated


async def apply_recording_url(
    user_storage,
    bot,
    *,
    event_id: int,
    url: str,
    publish_to_group: bool = True,
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

    if publish_to_group:
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
        f'<a href="{safe_url}">Открыть запись в группе</a>'
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
    """Последний закончившийся эфир без записи (до 7 дней назад)."""
    now = datetime.now(MSK)
    events = await user_storage.list_club_schedule_events(
        from_at=now - timedelta(days=7),
        to_at=now,
        content_types=list(INVITEABLE_CONTENT_TYPES),
        limit=50,
    )
    best_id: Optional[int] = None
    best_end: Optional[datetime] = None
    for ev in events:
        if (ev.get("recording_url") or "").strip():
            continue
        if ev.get("is_cancelled"):
            continue
        starts = ev.get("starts_at")
        if not isinstance(starts, datetime):
            continue
        ends = ev.get("ends_at")
        ends_a = (
            _aware(ends) if isinstance(ends, datetime) else _aware(starts) + timedelta(hours=2)
        )
        if ends_a > now:
            continue  # ещё идёт
        if best_end is None or ends_a > best_end:
            best_end = ends_a
            best_id = int(ev["id"])
    return best_id
