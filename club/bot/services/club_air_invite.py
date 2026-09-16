"""ЭФ-2: личные приглашения и напоминания на эфиры (не prayer)."""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from aiogram.enums import ParseMode

from bot.services.club_engagement_policy import decide_club_outreach
from bot.texts import ru_club_schedule as sch_txt
from config import config
from storage.db.club_schedule import INVITEABLE_CONTENT_TYPES

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

# Окно «за сутки»: событие через 23–25 часов
_INVITE_HOURS_MIN = 23.0
_INVITE_HOURS_MAX = 25.0
# Окно «за час»: 50–70 минут
_REMIND_MINUTES_MIN = 50.0
_REMIND_MINUTES_MAX = 70.0


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=MSK)
    return dt.astimezone(MSK)


def format_air_invite_html(event: Dict[str, Any]) -> str:
    title = html.escape((event.get("title") or "Эфир").strip())
    ctype = sch_txt.CONTENT_TYPE_LABEL.get(
        event.get("content_type") or "other", "событие"
    )
    starts = _aware(event["starts_at"])
    when = starts.strftime("%d.%m.%Y в %H:%M")
    return (
        f"🎙 <b>Приглашение: {html.escape(ctype)}</b>\n\n"
        f"{title}\n\n"
        f"📅 <b>{when}</b> (МСК)\n\n"
        "Будет тепло и по делу — загляни, если получится. "
        "Если не выйдет — ничего страшного, запись потом тоже можно будет найти."
    )


def format_air_reminder_html(event: Dict[str, Any]) -> str:
    title = html.escape((event.get("title") or "Эфир").strip())
    starts = _aware(event["starts_at"])
    when = starts.strftime("%H:%M")
    return (
        f"⏰ <b>Через час</b>\n\n"
        f"{title}\n"
        f"Начало в <b>{when}</b> МСК.\n\n"
        "Если есть возможность — загляни."
    )


async def pick_invite_event_today(
    user_storage, *, now: Optional[datetime] = None
) -> Optional[Dict[str, Any]]:
    """Ближайшее приглашаемое событие в окне ~24ч; если несколько — только оно."""
    now = _aware(now or datetime.now(MSK))
    lo = now + timedelta(hours=_INVITE_HOURS_MIN)
    hi = now + timedelta(hours=_INVITE_HOURS_MAX)
    events = await user_storage.list_club_schedule_events(
        from_at=lo,
        to_at=hi,
        content_types=list(INVITEABLE_CONTENT_TYPES),
        limit=20,
    )
    return events[0] if events else None


async def pick_reminder_events(
    user_storage, *, now: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    now = _aware(now or datetime.now(MSK))
    lo = now + timedelta(minutes=_REMIND_MINUTES_MIN)
    hi = now + timedelta(minutes=_REMIND_MINUTES_MAX)
    return await user_storage.list_club_schedule_events(
        from_at=lo,
        to_at=hi,
        content_types=list(INVITEABLE_CONTENT_TYPES),
        limit=10,
    )


async def _maybe_complete_first_week_step4(user_storage, user_id: int) -> None:
    fw = await user_storage.get_club_first_week(user_id)
    if not fw or fw.get("ended_at"):
        return
    # step в таблице = последний выполненный; шаг 4 — следующий при step==3
    if int(fw.get("step") or 0) != 3:
        return
    try:
        await user_storage.mark_first_week_step(user_id, 4)
        await user_storage.log_interaction(
            user_id=user_id,
            event_category="first_week",
            event_type="step_done",
            data={"step": 4, "via": "air_invite"},
            source="air_invite",
            outcome="success",
        )
    except Exception as e:
        logger.warning("first_week step4 via air uid=%s: %s", user_id, e)


async def run_air_invite_batch(
    user_storage,
    bot,
    *,
    api_key: str = "",
) -> Dict[str, int]:
    """Рассылка приглашений за ~сутки до эфира."""
    if not getattr(config, "CLUB_AIR_INVITE_ENABLED", True):
        return {"sent": 0, "skipped": 0, "failed": 0}

    event = await pick_invite_event_today(user_storage)
    if not event:
        return {"sent": 0, "skipped": 0, "failed": 0, "no_event": 1}

    event_id = int(event["id"])
    html_body = format_air_invite_html(event)
    recipients = await user_storage.list_user_ids_with_active_license()
    now = datetime.now(MSK)
    since_dm = now - timedelta(hours=24)
    sent = skipped = failed = 0

    await user_storage.log_interaction(
        user_id=0,
        event_category="air",
        event_type="air_invite_batch_start",
        data={"event_id": event_id, "recipients": len(recipients)},
        source="air_invite",
        outcome="success",
    )

    for uid in recipients:
        # не писать тем, кто писал боту за сутки
        if await user_storage.user_wrote_bot_since(uid, since=since_dm):
            skipped += 1
            continue

        decision = await decide_club_outreach(
            user_storage, uid, kind="air_invite", api_key=api_key
        )
        if not decision.allow:
            skipped += 1
            continue

        if not await user_storage.try_claim_air_send(
            event_id=event_id, user_id=uid, kind="invite"
        ):
            skipped += 1
            continue

        try:
            await bot.send_message(
                uid, html_body, parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )
            sent += 1
            await user_storage.increment_proactive_sent_today(uid)
            await _maybe_complete_first_week_step4(user_storage, uid)
            await user_storage.log_interaction(
                user_id=uid,
                event_category="air",
                event_type="air_invite_sent",
                data={"event_id": event_id},
                source="air_invite",
                outcome="success",
            )
        except Exception as e:
            failed += 1
            logger.warning("air invite send uid=%s: %s", uid, e)

    logger.info(
        "air invite event=%s sent=%s skip=%s fail=%s",
        event_id,
        sent,
        skipped,
        failed,
    )
    return {"sent": sent, "skipped": skipped, "failed": failed, "event_id": event_id}


async def run_air_reminder_batch(
    user_storage,
    bot,
    *,
    api_key: str = "",
) -> Dict[str, int]:
    """Напоминание за час: тем, кто получил приглашение (без трека «открыл»)."""
    if not getattr(config, "CLUB_AIR_INVITE_ENABLED", True):
        return {"sent": 0, "skipped": 0, "failed": 0}

    events = await pick_reminder_events(user_storage)
    total = {"sent": 0, "skipped": 0, "failed": 0}
    if not events:
        return total

    # если в окне несколько — только ближайшее
    event = events[0]
    event_id = int(event["id"])
    html_body = format_air_reminder_html(event)
    invitees = await user_storage.list_air_invite_recipients(event_id, kind="invite")
    if not invitees:
        # fallback: активные с лицензией, писавшие в группу 3 дня
        gid = int(config.CLUB_GROUP_ID or 0)
        since = datetime.now(MSK) - timedelta(days=3)
        invitees = []
        for uid in await user_storage.list_user_ids_with_active_license():
            if gid and await user_storage.user_wrote_group_since(
                uid, club_group_id=gid, since=since
            ):
                invitees.append(uid)

    for uid in invitees:
        decision = await decide_club_outreach(
            user_storage, uid, kind="air_reminder", api_key=api_key
        )
        if not decision.allow:
            total["skipped"] += 1
            continue
        if not await user_storage.try_claim_air_send(
            event_id=event_id, user_id=uid, kind="reminder"
        ):
            total["skipped"] += 1
            continue
        try:
            await bot.send_message(
                uid, html_body, parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )
            total["sent"] += 1
            await user_storage.increment_proactive_sent_today(uid)
            await user_storage.log_interaction(
                user_id=uid,
                event_category="air",
                event_type="air_reminder_sent",
                data={"event_id": event_id},
                source="air_invite",
                outcome="success",
            )
        except Exception as e:
            total["failed"] += 1
            logger.warning("air reminder uid=%s: %s", uid, e)

    logger.info("air reminder event=%s %s", event_id, total)
    return total
