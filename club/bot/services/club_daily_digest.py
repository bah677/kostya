"""Ежедневный дайджест активности клубной группы для участников."""

from __future__ import annotations

import html as html_mod
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from bot.services.club_churn_report import load_aboutclub_text
from bot.services.club_report_v2.deepseek_blocks import _chat
from bot.services.llm_call_logger import logged_deepseek_chat
from bot.services.llm_request_kinds import CLUB_DIGEST_BASE
from bot.services.report_exclude import sql_exclude_users
from bot.texts.prompts.club_daily_digest import (
    RETRY_USER_SUFFIX_NO_BLOCKQUOTE,
    SCRIPTURE_BLOCKQUOTE_RULES,
    build_digest_system_prompt,
    retry_user_suffix_too_long,
)
from bot.texts.ru_club_digest import (
    digest_skip_llm_empty,
    digest_skip_too_few_messages,
    digest_skip_too_few_participants,
    digest_title_line,
)
from bot.utils.telegram_html import sanitize_telegram_html

logger = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")

_DIGEST_LLM_MAX_ATTEMPTS = 3
_DIGEST_MAX_CHARS = 2000

def _digest_html_has_blockquote(html: str) -> bool:
    return bool(re.search(r"<blockquote\b", html, re.IGNORECASE))


def digest_report_date_msk(*, now: Optional[datetime] = None) -> date:
    """Календарный вчера по Москве — день, за который собирается дайджест."""
    ref = now.astimezone(MSK) if now else datetime.now(MSK)
    return (ref - timedelta(days=1)).date()


def _digest_body_max_chars() -> int:
    """Лимит тела дайджеста с учётом заголовка при публикации."""
    overhead = len(digest_title_line(report_date=digest_report_date_msk())) + 2
    return max(400, _DIGEST_MAX_CHARS - overhead)


def _digest_validation_errors(html: str) -> List[str]:
    errs: List[str] = []
    if not _digest_html_has_blockquote(html):
        errs.append("no_blockquote")
    if len(html) > _digest_body_max_chars():
        errs.append("too_long")
    return errs


def _retry_suffix_for_errors(errs: List[str], *, actual_len: int = 0) -> str:
    parts: List[str] = []
    if "no_blockquote" in errs:
        parts.append(RETRY_USER_SUFFIX_NO_BLOCKQUOTE)
    if "too_long" in errs:
        limit = _digest_body_max_chars()
        parts.append(
            retry_user_suffix_too_long(
                limit_chars=limit, actual_len=actual_len or 9999
            )
        )
    return "".join(parts)


@dataclass(frozen=True)
class ClubDigestBuildResult:
    html: str
    message_count: int
    participant_count: int
    skipped: bool
    skip_reason: str = ""


def _mention(user_id: int, username: Optional[str], first_name: Optional[str]) -> str:
    if username:
        un = username.lstrip("@")
        return f"@{html_mod.escape(un)}"
    fn = (first_name or "").strip()
    if fn:
        return html_mod.escape(fn)
    return f"id{user_id}"


async def fetch_club_group_messages(
    pool,
    *,
    club_group_id: int,
    report_date: Optional[date] = None,
    exclude_topic_id: int = 0,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Сообщения участников в клубной группе за календарные сутки (МСК), по умолчанию — вчера."""
    if not club_group_id:
        return []
    day = report_date or digest_report_date_msk()
    topic_filter = ""
    args: list = [club_group_id, day]
    n = 3
    if exclude_topic_id > 0:
        topic_filter = (
            f" AND COALESCE((m.metadata->>'message_thread_id')::bigint, 0) <> ${n}"
        )
        args.append(exclude_topic_id)
        n += 1
    exclude_sql, exclude_ids = sql_exclude_users("m.user_id", start_param=n)
    args.extend(exclude_ids)
    n += len(exclude_ids)
    args.append(limit)
    limit_ph = n
    sql = f"""
        SELECT
            m.user_id,
            u.username,
            u.first_name,
            m.content,
            m.created_at,
            COALESCE((m.metadata->>'message_thread_id')::bigint, 0) AS topic_id
        FROM messages m
        LEFT JOIN users u ON u.user_id = m.user_id
        WHERE m.chat_id = $1
          AND m.role = 'user'
          AND m.deleted_at IS NULL
          AND COALESCE(TRIM(m.content), '') <> ''
          AND (m.created_at AT TIME ZONE 'Europe/Moscow')::date = $2::date
          {topic_filter}
          {exclude_sql}
        ORDER BY m.created_at ASC
        LIMIT ${limit_ph}
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


def _format_messages_blob(rows: List[Dict[str, Any]]) -> tuple[str, int]:
    """Текст переписки для LLM и число уникальных авторов."""
    lines: List[str] = []
    authors: set = set()
    for r in rows:
        uid = r.get("user_id")
        if uid:
            authors.add(int(uid))
        who = _mention(
            int(uid or 0),
            r.get("username"),
            r.get("first_name"),
        )
        ts = r.get("created_at")
        if hasattr(ts, "strftime"):
            ts_s = ts.astimezone(MSK).strftime("%d.%m %H:%M")
        else:
            ts_s = str(ts)
        content = (r.get("content") or "").replace("\n", " ").strip()
        if len(content) > 600:
            content = content[:599] + "…"
        lines.append(f"{who} ({ts_s}): {content}")
    return "\n".join(lines), len(authors)


async def generate_club_member_digest_html(
    *,
    api_key: str,
    messages_blob: str,
    stats_line: str,
    about_club: str,
    report_date: date,
    min_messages: int = 3,
    user_storage=None,
) -> Optional[str]:
    """Генерирует HTML дайджест для топика клуба."""
    if not messages_blob.strip():
        return None

    day_label = report_date.strftime("%d.%m.%Y")
    sys_p = build_digest_system_prompt(
        day_label=day_label,
        body_limit=_digest_body_max_chars(),
    )
    user_base = (
        f"День дайджеста (вчера, МСК): {day_label}\n"
        f"Статистика: {stats_line}\n\n"
        f"<<<О_КЛУБЕ>>>\n{(about_club or '').strip()[:60_000]}\n<<<КОНЕЦ>>>\n\n"
        f"<<<ПЕРЕПИСКА_ЗА_ВЧЕРА>>>\n{messages_blob[:120_000]}\n<<<КОНЕЦ>>>"
    )
    retry_hint = ""
    for attempt in range(1, _DIGEST_LLM_MAX_ATTEMPTS + 1):
        user = user_base + retry_hint
        if user_storage:
            raw, _ = await logged_deepseek_chat(
                user_storage,
                user_id=0,
                request_kind=CLUB_DIGEST_BASE,
                api_key=api_key,
                system=sys_p,
                user=user,
                timeout_sec=360.0,
                temperature=0.55,
            )
        else:
            raw = await _chat(
                api_key=api_key,
                system=sys_p,
                user=user,
                timeout_sec=360.0,
                temperature=0.55,
            )
        if not raw:
            logger.warning(
                "club digest LLM: пустой ответ (попытка %s/%s)",
                attempt,
                _DIGEST_LLM_MAX_ATTEMPTS,
            )
            continue
        safe = sanitize_telegram_html(raw.strip())
        if not safe:
            continue
        errs = _digest_validation_errors(safe)
        if not errs:
            if attempt > 1:
                logger.info("club digest LLM: валидный ответ с попытки %s", attempt)
            return safe
        logger.warning(
            "club digest LLM: %s (len=%s, попытка %s/%s)",
            ",".join(errs),
            len(safe),
            attempt,
            _DIGEST_LLM_MAX_ATTEMPTS,
        )
        retry_hint += _retry_suffix_for_errors(errs, actual_len=len(safe))
    return None


async def build_club_daily_digest(
    pool,
    *,
    club_group_id: int,
    api_key: str,
    lookback_hours: int = 24,  # legacy env; период = календарный вчера (МСК)
    digest_topic_id: int = 0,
    min_messages: int = 5,
    min_participants: int = 2,
    user_storage=None,
) -> ClubDigestBuildResult:
    report_date = digest_report_date_msk()
    rows = await fetch_club_group_messages(
        pool,
        club_group_id=club_group_id,
        report_date=report_date,
        exclude_topic_id=digest_topic_id,
    )
    if len(rows) < min_messages:
        return ClubDigestBuildResult(
            html="",
            message_count=len(rows),
            participant_count=0,
            skipped=True,
            skip_reason=digest_skip_too_few_messages(
                message_count=len(rows), min_messages=min_messages
            ),
        )

    blob, n_authors = _format_messages_blob(rows)
    if n_authors < min_participants:
        return ClubDigestBuildResult(
            html="",
            message_count=len(rows),
            participant_count=n_authors,
            skipped=True,
            skip_reason=digest_skip_too_few_participants(
                participant_count=n_authors, min_participants=min_participants
            ),
        )

    day_label = report_date.strftime("%d.%m.%Y")
    stats = (
        f"За {day_label} (МСК): сообщений {len(rows)}; "
        f"уникальных авторов: {n_authors}"
    )
    about = load_aboutclub_text()
    html_body = await generate_club_member_digest_html(
        api_key=api_key,
        messages_blob=blob,
        stats_line=stats,
        about_club=about,
        report_date=report_date,
        user_storage=user_storage,
    )
    if not html_body:
        return ClubDigestBuildResult(
            html="",
            message_count=len(rows),
            participant_count=n_authors,
            skipped=True,
            skip_reason=digest_skip_llm_empty(),
        )

    title = digest_title_line(report_date=report_date)
    full = f"{title}\n\n{html_body}"
    return ClubDigestBuildResult(
        html=full,
        message_count=len(rows),
        participant_count=n_authors,
        skipped=False,
    )
