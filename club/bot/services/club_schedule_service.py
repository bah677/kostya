"""Расписание клуба: применение, форматирование, промпт для агента."""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from bot.services.club_schedule_extractor import (
    ExtractedScheduleEvent,
    extract_schedule_events_from_text,
    text_looks_like_schedule,
    schedule_topic_input_eligible,
)
from bot.texts import ru_club_schedule as sch_txt
from config import config

logger = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")
MIN_AUTO_CONFIDENCE = 0.55
TOPIC_CORRECTION_MIN_CONFIDENCE = 0.5


@dataclass
class ScheduleApplyResult:
    applied: bool
    summary: str
    event_ids: List[int]


def build_telegram_message_link(chat_id: int, message_id: int) -> str:
    s = str(chat_id)
    if s.startswith("-100"):
        internal = s[4:]
    elif s.startswith("-"):
        internal = s[1:]
    else:
        internal = s
    return f"https://t.me/c/{internal}/{message_id}"


def _fmt_dt_msk(dt: Any) -> str:
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=MSK)
        return dt.astimezone(MSK).strftime("%d.%m %H:%M")
    return str(dt)


def _type_label(content_type: str) -> str:
    return sch_txt.CONTENT_TYPE_LABEL.get(content_type or "other", "событие")


async def apply_extracted_events(
    user_storage,
    events: List[ExtractedScheduleEvent],
    *,
    source: str,
    source_admin_id: Optional[int] = None,
    source_message_id: Optional[int] = None,
    source_chat_id: Optional[int] = None,
    group_message_link: Optional[str] = None,
    raw_text: Optional[str] = None,
    confidence: float = 1.0,
) -> ScheduleApplyResult:
    ids: List[int] = []
    lines: List[str] = []

    for ev in events:
        if ev.action == "cancel" or ev.is_cancelled:
            n = await user_storage.cancel_club_schedule_near(
                starts_at=ev.starts_at,
                title_hint=ev.title,
            )
            lines.append(
                f"отмена: {_fmt_dt_msk(ev.starts_at)} — {ev.title} ({n} записей)"
            )
            continue

        eid = await user_storage.insert_club_schedule_event(
            starts_at=ev.starts_at,
            ends_at=ev.ends_at,
            title=ev.title,
            content_type=ev.content_type,
            source=source,
            source_message_id=source_message_id,
            source_chat_id=source_chat_id,
            source_admin_id=source_admin_id,
            group_message_link=group_message_link,
            raw_text=raw_text,
            confidence=confidence,
        )
        if eid:
            ids.append(eid)
            lines.append(
                f"{_fmt_dt_msk(ev.starts_at)} — {_type_label(ev.content_type)}: {ev.title}"
            )

    summary = "\n".join(lines) if lines else ""
    return ScheduleApplyResult(
        applied=bool(lines),
        summary=summary,
        event_ids=ids,
    )


def build_schedule_prompt_addon(events: List[Dict[str, Any]]) -> str:
    if not events:
        return (
            "=== РАСПИСАНИЕ КЛУБА (МСК, из БД) ===\n"
            "(на ближайшие дни событий в базе нет — не называй конкретное время эфиров; "
            "скажи следить за группой или уточнить у /support)"
        )

    lines = [
        "=== РАСПИСАНИЕ КЛУБА (МСК, из БД — единственный источник дат/времени) ===",
    ]
    for ev in events:
        link = (ev.get("group_message_link") or "").strip()
        link_part = f" | ссылка: {link}" if link else ""
        lines.append(
            f"- {_fmt_dt_msk(ev.get('starts_at'))} — "
            f"{_type_label(ev.get('content_type', ''))}: "
            f"{ev.get('title', '')}{link_part}"
        )
    lines.append(
        "Не придумывай другие даты и эфиры — только строки выше."
    )
    return "\n".join(lines)


async def fetch_schedule_for_prompt(user_storage, *, days: int = 7) -> str:
    events = await _fetch_schedule_events(user_storage, days=days)
    return build_schedule_prompt_addon(events)


async def fetch_schedule_allowed_links(user_storage, *, days: int = 7) -> List[str]:
    events = await _fetch_schedule_events(user_storage, days=days)
    out: List[str] = []
    for ev in events:
        link = (ev.get("group_message_link") or "").strip()
        if link and link not in out:
            out.append(link)
    return out


async def _fetch_schedule_events(user_storage, *, days: int = 7) -> List[Dict[str, Any]]:
    now = datetime.now(MSK)
    end = now + timedelta(days=days)
    return await user_storage.list_club_schedule_events(
        from_at=now - timedelta(hours=1),
        to_at=end,
    )


async def format_schedule_topic_digest(
    user_storage,
    *,
    days: Optional[int] = None,
) -> str:
    """HTML для вечернего поста в топик «Расписание» админ-группы."""
    span = days if days is not None else config.CLUB_SCHEDULE_TOPIC_DIGEST_DAYS
    now = datetime.now(MSK)
    start = now - timedelta(hours=1)
    end = now + timedelta(days=span)
    events = await user_storage.list_club_schedule_events(from_at=start, to_at=end)

    lines = [
        sch_txt.SCHEDULE_TOPIC_DIGEST_HEADER.format(days=span),
        f"<i>Обновлено {now.strftime('%d.%m.%Y %H:%M')} МСК</i>",
        "",
        sch_txt.SCHEDULE_TOPIC_DIGEST_INTRO,
        "",
    ]
    if not events:
        lines.append(sch_txt.SCHEDULE_EMPTY)
    else:
        for ev in events:
            lines.append(
                f"• <b>{_fmt_dt_msk(ev.get('starts_at'))}</b> — "
                f"{html.escape(_type_label(ev.get('content_type', '')))}: "
                f"{html.escape(str(ev.get('title') or ''))}"
            )
    return "\n".join(lines)


async def format_schedule_admin_message(
    user_storage,
    *,
    mode: str = "week",
) -> str:
    now = datetime.now(MSK)
    if mode == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        header = sch_txt.SCHEDULE_HEADER_TODAY
    elif mode == "raw":
        rows = await user_storage.list_recent_club_schedule_raw(limit=15)
        if not rows:
            return sch_txt.SCHEDULE_EMPTY
        lines = [sch_txt.SCHEDULE_HEADER_RAW, ""]
        for ev in rows:
            flag = " ❌" if ev.get("is_cancelled") else ""
            conf = ev.get("confidence")
            lines.append(
                f"• {_fmt_dt_msk(ev.get('starts_at'))} — "
                f"{html.escape(str(ev.get('title') or ''))} "
                f"({html.escape(str(ev.get('source') or ''))}, conf={conf}){flag}"
            )
        lines.append(sch_txt.SCHEDULE_FOOTER)
        return "\n".join(lines)
    else:
        start = now - timedelta(hours=1)
        end = now + timedelta(days=7)
        header = sch_txt.SCHEDULE_HEADER_WEEK

    events = await user_storage.list_club_schedule_events(
        from_at=start, to_at=end
    )
    if not events:
        return sch_txt.SCHEDULE_EMPTY + sch_txt.SCHEDULE_FOOTER

    lines = [header, ""]
    for ev in events:
        lines.append(
            f"• <b>{_fmt_dt_msk(ev.get('starts_at'))}</b> — "
            f"{html.escape(_type_label(ev.get('content_type', '')))}: "
            f"{html.escape(str(ev.get('title') or ''))}"
        )
    lines.append(sch_txt.SCHEDULE_FOOTER)
    return "\n".join(lines)


async def index_schedule_from_group_message(
    user_storage,
    llm_client,
    message,
) -> Optional[ScheduleApplyResult]:
    """Парсит пост админа в группе клуба и пишет в расписание."""
    if not message.from_user or message.from_user.is_bot:
        return None
    uid = message.from_user.id
    if not await user_storage.is_telegram_admin_id(uid):
        return None

    text = (message.text or message.caption or "").strip()
    if not text or len(text) < 10:
        return None
    if not text_looks_like_schedule(text):
        return None

    events, confidence = await extract_schedule_events_from_text(
        llm_client, text, context_label="пост в группе клуба"
    )
    if confidence < MIN_AUTO_CONFIDENCE or not events:
        logger.info(
            "schedule group skip msg=%s conf=%.2f events=%s",
            message.message_id,
            confidence,
            len(events),
        )
        return None

    link = build_telegram_message_link(message.chat.id, message.message_id)
    return await apply_extracted_events(
        user_storage,
        events,
        source="group_message",
        source_admin_id=uid,
        source_message_id=message.message_id,
        source_chat_id=message.chat.id,
        group_message_link=link,
        raw_text=text[:4000],
        confidence=confidence,
    )


async def try_apply_schedule_from_admin_dm(
    user_storage,
    llm_client,
    admin_id: int,
    text: str,
) -> Optional[ScheduleApplyResult]:
    if not await user_storage.is_telegram_admin_id(admin_id):
        return None
    if not text_looks_like_schedule(text):
        return None

    events, confidence = await extract_schedule_events_from_text(
        llm_client, text, context_label="правка расписания в личке"
    )
    if confidence < MIN_AUTO_CONFIDENCE or not events:
        return None

    return await apply_extracted_events(
        user_storage,
        events,
        source="admin_dm",
        source_admin_id=admin_id,
        raw_text=text[:4000],
        confidence=confidence,
    )


async def try_apply_schedule_from_admin_topic(
    user_storage,
    llm_client,
    *,
    author_id: int,
    text: str,
    chat_id: int,
    message_id: int,
) -> Optional[ScheduleApplyResult]:
    """Правка расписания нативным текстом в топике админ-группы."""
    body = (text or "").strip()
    if len(body) < 5:
        return None
    if not schedule_topic_input_eligible(body):
        return None

    events, confidence = await extract_schedule_events_from_text(
        llm_client,
        body,
        context_label="правка в топике расписания админ-группы",
    )
    if confidence < TOPIC_CORRECTION_MIN_CONFIDENCE or not events:
        logger.info(
            "schedule topic skip uid=%s conf=%.2f events=%s",
            author_id,
            confidence,
            len(events),
        )
        return None

    link = build_telegram_message_link(chat_id, message_id)
    return await apply_extracted_events(
        user_storage,
        events,
        source="admin_topic",
        source_admin_id=author_id,
        source_message_id=message_id,
        source_chat_id=chat_id,
        group_message_link=link,
        raw_text=body[:4000],
        confidence=confidence,
    )


def schedule_topic_reply_html(result: Optional[ScheduleApplyResult]) -> str:
    if result and result.applied:
        safe_summary = html.escape(result.summary or "")
        return sch_txt.SCHEDULE_TOPIC_APPLIED.format(summary=safe_summary)
    return sch_txt.SCHEDULE_TOPIC_NOT_UNDERSTOOD


def schedule_admin_dm_addon(result: ScheduleApplyResult) -> str:
    if not result.applied:
        return ""
    return (
        "=== ОБНОВЛЕНИЕ РАСПИСАНИЯ (только что применено из сообщения админа) ===\n"
        f"{result.summary}\n"
        "Кратко подтверди админу, что записал в расписание. Не выдумывай других изменений."
    )
