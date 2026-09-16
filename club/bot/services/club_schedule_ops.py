"""ЭФ-1: материализация повторов + сторож горизонта + связность."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from bot.utils.admin_channel import send_admin_html_message
from config import config

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

_HORIZON_KEY = "horizon_watch"
_COHERENCE_KEY = "coherence_watch"
_EXPAND_DAYS = 14


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=MSK)
    return dt.astimezone(MSK)


async def expand_recurrence_templates(user_storage) -> int:
    """Создаёт экземпляры daily/weekly на горизонт _EXPAND_DAYS."""
    templates = await user_storage.list_schedule_recurrence_templates()
    if not templates:
        return 0
    now = datetime.now(MSK)
    created = 0
    for tpl in templates:
        recurrence = (tpl.get("recurrence") or "none").lower()
        starts = _aware(tpl["starts_at"])
        until = tpl.get("recurrence_until")
        series_id = int(tpl["id"])
        for offset in range(0, _EXPAND_DAYS + 1):
            day = (now.date() + timedelta(days=offset))
            if until and day > until:
                break
            if recurrence == "weekly":
                dow = tpl.get("recurrence_dow")
                if dow is None:
                    dow = starts.weekday()  # Mon=0
                if day.weekday() != int(dow):
                    continue
            elif recurrence != "daily":
                continue

            day_start = datetime(day.year, day.month, day.day, tzinfo=MSK)
            day_end = day_start + timedelta(days=1)
            if await user_storage.has_schedule_instance_on_day(
                series_id=series_id, day_start=day_start, day_end=day_end
            ):
                continue

            # одноразовое исключение в тот же слот ±90 мин — не плодим дубль
            slot = day_start.replace(
                hour=starts.hour, minute=starts.minute, second=0, microsecond=0
            )
            near = await user_storage.list_club_schedule_events(
                from_at=slot - timedelta(minutes=90),
                to_at=slot + timedelta(minutes=90),
                limit=5,
            )
            if near:
                continue

            ends = tpl.get("ends_at")
            ends_at = None
            if isinstance(ends, datetime):
                ends_a = _aware(ends)
                duration = ends_a - starts
                if duration.total_seconds() > 0:
                    ends_at = slot + duration

            eid = await user_storage.insert_club_schedule_event(
                starts_at=slot,
                ends_at=ends_at,
                title=tpl.get("title") or "Событие",
                content_type=tpl.get("content_type") or "other",
                source="recurrence",
                raw_text=f"series:{series_id}",
                confidence=float(tpl.get("confidence") or 1.0),
                recurrence="none",
                series_id=series_id,
            )
            if eid:
                created += 1
                await user_storage.log_interaction(
                    user_id=0,
                    event_category="schedule",
                    event_type="schedule_event_created",
                    data={
                        "event_id": eid,
                        "source": "recurrence",
                        "series_id": series_id,
                        "confidence": float(tpl.get("confidence") or 1.0),
                    },
                    source="schedule_recurrence",
                    outcome="success",
                )
    if created:
        logger.info("schedule recurrence expanded: %s instances", created)
    return created


async def run_horizon_watch(user_storage, bot) -> Optional[str]:
    """Если в ближайшие 7 дней покрыто < 3 дней — спросить админов."""
    today = datetime.now(MSK).date()
    covered = await user_storage.count_schedule_covered_days(from_day=today, days=7)
    if covered >= 3:
        return None

    state = await user_storage.get_schedule_ops_state(_HORIZON_KEY)
    last_iso = state.get("last_alert_at")
    if last_iso:
        try:
            last = datetime.fromisoformat(str(last_iso))
            if last.tzinfo is None:
                last = last.replace(tzinfo=MSK)
            if datetime.now(MSK) - last.astimezone(MSK) < timedelta(hours=48):
                return None
        except ValueError:
            pass

    max_d = await user_storage.max_future_schedule_date()
    until_s = max_d.strftime("%d %B").replace(
        "January", "января"
    ) if False else (max_d.strftime("%d.%m.%Y") if max_d else "скоро")
    # простой формат даты без локали
    text = (
        f"⚠️ Расписание кончается {until_s}.\n"
        f"В ближайшие 7 дней покрыто только <b>{covered}</b> дн. (нужно ≥ 3).\n\n"
        "Что дальше? Напишите обычными словами."
    )
    topic_id = config.CLUB_SCHEDULE_ADMIN_TOPIC_ID
    ok = await send_admin_html_message(
        bot,
        text,
        thread_id=topic_id if topic_id and topic_id > 0 else None,
    )
    # дубль в админ-чат без топика — тот же канал; если есть ADMIN_CHANNEL — уже там
    await user_storage.set_schedule_ops_state(
        _HORIZON_KEY,
        {
            "last_alert_at": datetime.now(MSK).isoformat(),
            "covered": covered,
            "max_date": str(max_d) if max_d else None,
        },
    )
    await user_storage.log_interaction(
        user_id=0,
        event_category="schedule",
        event_type="schedule_gap_detected",
        data={"covered_days": covered, "kind": "horizon"},
        source="schedule_ops",
        outcome="success",
    )
    return text if ok else None


async def run_coherence_watch(user_storage, bot) -> Optional[str]:
    """Много упоминаний эфира в группе, а air/qa в БД нет."""
    gid = int(config.CLUB_GROUP_ID or 0)
    if not gid:
        return None
    now = datetime.now(MSK)
    since = now - timedelta(days=7)
    mentions = await user_storage.count_group_air_mentions(
        club_group_id=gid, since=since
    )
    if mentions <= 10:
        return None
    air_n = await user_storage.count_inviteable_events_in_range(
        from_at=since, to_at=now + timedelta(days=1)
    )
    # «за тот же период» — события air/qa за 7 дней (прошлые+сегодня)
    if air_n > 0:
        return None

    state = await user_storage.get_schedule_ops_state(_COHERENCE_KEY)
    last_iso = state.get("last_alert_at")
    if last_iso:
        try:
            last = datetime.fromisoformat(str(last_iso))
            if last.tzinfo is None:
                last = last.replace(tzinfo=MSK)
            if now - last.astimezone(MSK) < timedelta(hours=48):
                return None
        except ValueError:
            pass

    text = (
        f"⚠️ В группе за 7 дней эфиры упоминали <b>{mentions}</b> раз, "
        "а в расписании за это время нет событий типа air/qa/…\n\n"
        "Добавьте эфир обычными словами в топик «Расписание»."
    )
    topic_id = config.CLUB_SCHEDULE_ADMIN_TOPIC_ID
    await send_admin_html_message(
        bot,
        text,
        thread_id=topic_id if topic_id and topic_id > 0 else None,
    )
    await user_storage.set_schedule_ops_state(
        _COHERENCE_KEY,
        {"last_alert_at": now.isoformat(), "mentions": mentions},
    )
    await user_storage.log_interaction(
        user_id=0,
        event_category="schedule",
        event_type="schedule_gap_detected",
        data={"mentions": mentions, "kind": "coherence"},
        source="schedule_ops",
        outcome="success",
    )
    return text
