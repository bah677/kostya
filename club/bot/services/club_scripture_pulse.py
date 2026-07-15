"""Периодическая цитата из Писания по переписке в клубной группе."""

from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, List, Optional, Sequence
from zoneinfo import ZoneInfo

from bot.services.club_daily_digest import MSK, _format_messages_blob
from bot.services.club_report_v2.deepseek_blocks import _chat
from bot.services.report_exclude import sql_exclude_users
from bot.texts.prompts.club_scripture_pulse import (
    RETRY_USER_SUFFIX_DUPLICATE,
    RETRY_USER_SUFFIX_TOO_STRICT,
    SCRIPTURE_QUOTE_SYSTEM_PROMPT,
    format_recent_pulse_quotes_user_block,
)
from bot.texts.ru_club_scripture_pulse import (
    scripture_pulse_skip_llm_empty,
    scripture_pulse_skip_too_few_messages,
)
from bot.utils.telegram_html import sanitize_telegram_html

logger = logging.getLogger(__name__)

DEFAULT_PULSE_HOURS: tuple[int, ...] = (7, 9, 12, 15, 18, 21)

_STATE_FILE = Path(__file__).resolve().parents[2] / "data" / "club_scripture_pulse_state.json"
_RECENT_QUOTES_MAX = 40


@dataclass(frozen=True)
class ScripturePulseResult:
    html: str
    message_count: int
    skipped: bool
    skip_reason: str = ""
    since_at: Optional[datetime] = None


def parse_pulse_hours(raw: str) -> tuple[int, ...]:
    out: list[int] = []
    for part in (raw or "").replace(";", ",").split(","):
        p = part.strip()
        if not p:
            continue
        h = int(p)
        if 0 <= h <= 23:
            out.append(h)
    return tuple(sorted(set(out))) if out else DEFAULT_PULSE_HOURS


def pulse_state_path() -> Path:
    return _STATE_FILE


def _load_pulse_state(path: Optional[Path] = None) -> dict:
    p = path or pulse_state_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def _save_pulse_state(data: dict, path: Optional[Path] = None) -> None:
    p = path or pulse_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_last_pulse_at(path: Optional[Path] = None) -> Optional[datetime]:
    raw = _load_pulse_state(path).get("last_run_at")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=MSK)
        return dt.astimezone(MSK)
    except (TypeError, ValueError):
        return None


def load_recent_pulse_quote_refs(path: Optional[Path] = None) -> list[str]:
    """Ссылки на места в Писании из недавних отправок в топик."""
    raw = _load_pulse_state(path).get("recent_refs")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        s = str(item).strip()
        if s:
            out.append(s)
    return out[-_RECENT_QUOTES_MAX:]


def load_recent_pulse_plain_texts(path: Optional[Path] = None) -> list[str]:
    raw = _load_pulse_state(path).get("recent_plain")
    if not isinstance(raw, list):
        return []
    return [str(x).strip().lower() for x in raw if str(x).strip()][-_RECENT_QUOTES_MAX:]


def _normalize_scripture_ref(ref: str) -> str:
    return re.sub(r"\s+", " ", (ref or "").lower().strip("() "))


def extract_scripture_ref_from_html(html: str) -> str:
    """Книга/глава:стих из <i>(…)</i> внутри blockquote."""
    m = re.search(
        r"<blockquote\b[^>]*>.*?<i>\s*\(([^)]+)\)\s*</i>",
        html or "",
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        return m.group(1).strip()
    m2 = re.search(r"<i>\s*\(([^)]+)\)\s*</i>", html or "", re.IGNORECASE)
    return m2.group(1).strip() if m2 else ""


def _extract_blockquote_plain_text(html: str) -> str:
    m = re.search(
        r"<blockquote\b[^>]*>(.*?)</blockquote>",
        html or "",
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return ""
    inner = re.sub(r"<[^>]+>", " ", m.group(1))
    return re.sub(r"\s+", " ", inner).strip().lower()


def is_duplicate_pulse_quote(
    html: str,
    recent_refs: list[str],
    *,
    recent_plain: Optional[list[str]] = None,
) -> bool:
    ref = extract_scripture_ref_from_html(html)
    norm_ref = _normalize_scripture_ref(ref)
    recent_norm = {_normalize_scripture_ref(r) for r in recent_refs if r}
    if norm_ref and norm_ref in recent_norm:
        return True
    plain = _extract_blockquote_plain_text(html)
    if len(plain) >= 20 and plain in set(recent_plain or []):
        return True
    return False


def append_recent_pulse_quote(html: str, path: Optional[Path] = None) -> None:
    ref = extract_scripture_ref_from_html(html)
    plain = _extract_blockquote_plain_text(html)
    if not ref and not plain:
        return
    data = _load_pulse_state(path)
    refs = load_recent_pulse_quote_refs(path)
    plains = load_recent_pulse_plain_texts(path)
    if ref:
        norm_new = _normalize_scripture_ref(ref)
        refs = [r for r in refs if _normalize_scripture_ref(r) != norm_new]
        refs.append(ref)
    if plain:
        plains = [p for p in plains if p != plain]
        plains.append(plain)
    data["recent_refs"] = refs[-_RECENT_QUOTES_MAX:]
    data["recent_plain"] = plains[-_RECENT_QUOTES_MAX:]
    _save_pulse_state(data, path)


def save_last_pulse_at(when: datetime, path: Optional[Path] = None) -> None:
    data = _load_pulse_state(path)
    data["last_run_at"] = when.astimezone(MSK).isoformat()
    if "recent_refs" not in data:
        data["recent_refs"] = load_recent_pulse_quote_refs(path)
    if "recent_plain" not in data:
        data["recent_plain"] = load_recent_pulse_plain_texts(path)
    _save_pulse_state(data, path)


def resolve_pulse_since(
    *,
    last_run: Optional[datetime],
    now_msk: datetime,
    slot_hour: int,
    pulse_hours: Sequence[int],
) -> datetime:
    if last_run is not None:
        return last_run.astimezone(MSK)

    hours = sorted(set(int(h) for h in pulse_hours))
    prev = [h for h in hours if h < slot_hour]
    if prev:
        h = max(prev)
        return now_msk.replace(hour=h, minute=0, second=0, microsecond=0)
    last_yesterday = max(hours) if hours else 21
    d = now_msk.date() - timedelta(days=1)
    return datetime.combine(d, time(last_yesterday, 0), tzinfo=MSK)


def pick_random_pulse_minutes(
    *,
    minute_min: int,
    minute_max: int,
    pulse_hours: Sequence[int],
) -> dict[int, int]:
    lo = max(0, min(minute_min, minute_max))
    hi = max(lo, minute_max)
    return {int(h): random.randint(lo, hi) for h in pulse_hours}


async def fetch_club_group_messages_since(
    pool,
    *,
    club_group_id: int,
    since: datetime,
    exclude_topic_id: int = 0,
    limit: int = 400,
) -> List[Dict[str, Any]]:
    if not club_group_id:
        return []
    since_utc = since.astimezone(ZoneInfo("UTC"))
    topic_filter = ""
    args: list = [club_group_id, since_utc]
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
          AND m.created_at > $2::timestamptz
          {topic_filter}
          {exclude_sql}
        ORDER BY m.created_at ASC
        LIMIT ${limit_ph}
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


def _has_blockquote(html: str) -> bool:
    return bool(re.search(r"<blockquote\b", html, re.IGNORECASE))


async def generate_scripture_quote_html(
    *,
    api_key: str,
    messages_blob: str,
    recent_refs: Optional[list[str]] = None,
    recent_plain: Optional[list[str]] = None,
    max_attempts: int = 4,
) -> Optional[str]:
    if not messages_blob.strip():
        return None

    recent = list(recent_refs or [])
    plain_history = list(recent_plain or [])
    sys_p = SCRIPTURE_QUOTE_SYSTEM_PROMPT
    user = (
        f"<<<ПЕРЕПИСКА>>>\n{messages_blob[:80_000]}\n<<<КОНЕЦ>>>"
        f"{format_recent_pulse_quotes_user_block(recent)}"
    )
    retry = ""
    temperature = 0.55
    for attempt in range(1, max_attempts + 1):
        raw = await _chat(
            api_key=api_key,
            system=sys_p,
            user=user + retry,
            timeout_sec=120.0,
            temperature=temperature,
            max_tokens=280,
        )
        if not raw:
            continue
        safe = sanitize_telegram_html(raw.strip())
        if not safe or not _has_blockquote(safe) or len(safe) > 900:
            retry = RETRY_USER_SUFFIX_TOO_STRICT
            continue
        if is_duplicate_pulse_quote(safe, recent):
            logger.info(
                "scripture_pulse: duplicate ref/text (attempt %s), retry",
                attempt,
            )
            retry = RETRY_USER_SUFFIX_DUPLICATE
            temperature = min(0.85, temperature + 0.1)
            continue
        return safe
    return None


async def run_club_scripture_pulse(
    pool,
    *,
    club_group_id: int,
    api_key: str,
    digest_topic_id: int,
    slot_hour: int,
    pulse_hours: Sequence[int],
    min_messages: int = 1,
    state_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> ScripturePulseResult:
    now_msk = (now or datetime.now(MSK)).astimezone(MSK)
    last = load_last_pulse_at(state_path)
    since = resolve_pulse_since(
        last_run=last,
        now_msk=now_msk,
        slot_hour=slot_hour,
        pulse_hours=pulse_hours,
    )

    rows = await fetch_club_group_messages_since(
        pool,
        club_group_id=club_group_id,
        since=since,
        exclude_topic_id=digest_topic_id,
    )
    if len(rows) < min_messages:
        return ScripturePulseResult(
            html="",
            message_count=len(rows),
            skipped=True,
            skip_reason=scripture_pulse_skip_too_few_messages(
                message_count=len(rows), min_messages=min_messages
            ),
            since_at=since,
        )

    blob, _ = _format_messages_blob(rows)
    recent_refs = load_recent_pulse_quote_refs(state_path)
    recent_plain = load_recent_pulse_plain_texts(state_path)
    html = await generate_scripture_quote_html(
        api_key=api_key,
        messages_blob=blob,
        recent_refs=recent_refs,
        recent_plain=recent_plain,
    )
    if not html:
        return ScripturePulseResult(
            html="",
            message_count=len(rows),
            skipped=True,
            skip_reason=scripture_pulse_skip_llm_empty(),
            since_at=since,
        )

    return ScripturePulseResult(
        html=html,
        message_count=len(rows),
        skipped=False,
        since_at=since,
    )


def commit_pulse_run(
    when: Optional[datetime] = None,
    path: Optional[Path] = None,
    *,
    sent_html: Optional[str] = None,
) -> None:
    p = path or pulse_state_path()
    save_last_pulse_at(when or datetime.now(MSK), p)
    if sent_html:
        append_recent_pulse_quote(sent_html, p)
