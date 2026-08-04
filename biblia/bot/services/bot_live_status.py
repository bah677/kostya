"""Светофор «можно ли деплоить»: живое состояние бота для /status и /adm."""

from __future__ import annotations

import html as html_mod
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

from bot.services.prayer_tts_queue import get_prayer_tts_queue

logger = logging.getLogger(__name__)

_MSK = ZoneInfo("Europe/Moscow")

# —— пороги (константы) ——
LIVE_WINDOW_MINUTES = 5
# > N уникальных user за окно → минимум жёлтый
LIVE_YELLOW_UNIQUE_USERS = 20
# > N сообщений (все роли) за окно → жёлтый (даже если юзеров мало)
LIVE_YELLOW_MESSAGES = 80


@dataclass(frozen=True)
class LiveVerdict:
    level: str  # green | yellow | red
    emoji: str
    title: str
    reason: str


def _msk_now() -> datetime:
    return datetime.now(_MSK)


def _fmt_dt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_MSK)
        else:
            dt = dt.astimezone(_MSK)
        return dt.strftime("%d.%m %H:%M")
    return html_mod.escape(str(value))


async def fetch_running_and_planned_campaigns(pool) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                c.id,
                c.name,
                c.status,
                c.scheduled_at,
                c.updated_at,
                COALESCE(c.sent_count, 0) AS sent_count,
                COALESCE(c.failed_count, 0) AS failed_count,
                COALESCE(a.audience_total, 0) AS audience_total,
                COALESCE(a.pending_count, 0) AS pending_count,
                COALESCE(a.processing_count, 0) AS processing_count
            FROM mailing_campaigns c
            LEFT JOIN LATERAL (
                SELECT
                    COUNT(*)::int AS audience_total,
                    COUNT(*) FILTER (WHERE status = 'pending')::int AS pending_count,
                    COUNT(*) FILTER (WHERE status = 'processing')::int AS processing_count
                FROM mailing_audience ma
                WHERE ma.campaign_id = c.id
            ) a ON TRUE
            WHERE c.status IN ('running', 'planned')
            ORDER BY
                CASE c.status WHEN 'running' THEN 0 ELSE 1 END,
                c.updated_at DESC NULLS LAST
            LIMIT 8
            """
        )
    return [dict(r) for r in rows]


async def fetch_recent_message_activity(
    pool, *, minutes: int = LIVE_WINDOW_MINUTES
) -> Dict[str, int]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*)::int AS messages,
                COUNT(DISTINCT user_id)::int AS unique_users,
                COUNT(*) FILTER (WHERE role = 'user')::int AS user_messages,
                COUNT(*) FILTER (WHERE role = 'assistant')::int AS bot_messages
            FROM messages
            WHERE created_at >= NOW() - ($1::text || ' minutes')::interval
              AND deleted_at IS NULL
            """,
            str(int(minutes)),
        )
    if not row:
        return {
            "messages": 0,
            "unique_users": 0,
            "user_messages": 0,
            "bot_messages": 0,
        }
    return {
        "messages": int(row["messages"] or 0),
        "unique_users": int(row["unique_users"] or 0),
        "user_messages": int(row["user_messages"] or 0),
        "bot_messages": int(row["bot_messages"] or 0),
    }


def snapshot_dialog_queues(bot_app: Any) -> Dict[str, int]:
    if bot_app is None:
        return {
            "users_with_queue": 0,
            "queued_messages": 0,
            "processing_users": 0,
        }
    users_with_queue = 0
    queued = 0
    try:
        queues = getattr(bot_app, "user_queues", {}) or {}
        for q in list(queues.values()):
            try:
                n = int(q.qsize())
            except Exception:
                n = 0
            if n > 0:
                users_with_queue += 1
                queued += n
        processing = len(getattr(bot_app, "processing_users", set()) or set())
    except Exception as e:
        logger.debug("dialog queue snapshot: %s", e)
        return {
            "users_with_queue": 0,
            "queued_messages": 0,
            "processing_users": 0,
        }
    return {
        "users_with_queue": users_with_queue,
        "queued_messages": queued,
        "processing_users": int(processing),
    }


def snapshot_prayer_tts() -> Dict[str, int]:
    try:
        q = get_prayer_tts_queue()
        waiting, running = q.snapshot()
        return {
            "waiting": int(waiting),
            "running": int(running),
            "max_concurrent": int(q.max_concurrent),
        }
    except Exception:
        return {"waiting": 0, "running": 0, "max_concurrent": 0}


def decide_verdict(
    *,
    campaigns: Sequence[Dict[str, Any]],
    activity: Dict[str, int],
    dialog: Dict[str, int],
    tts: Dict[str, int],
) -> LiveVerdict:
    running = [c for c in campaigns if (c.get("status") or "") == "running"]
    tts_busy = int(tts.get("waiting") or 0) + int(tts.get("running") or 0) > 0
    unique = int(activity.get("unique_users") or 0)
    msgs = int(activity.get("messages") or 0)
    dialog_busy = (
        int(dialog.get("users_with_queue") or 0) > 0
        or int(dialog.get("processing_users") or 0) > 0
    )

    if running:
        names = ", ".join(
            f"#{int(c.get('id') or 0)}" for c in running[:3]
        )
        return LiveVerdict(
            "red",
            "🔴",
            "Подождать",
            f"идёт рассылка ({names})",
        )
    if tts_busy:
        return LiveVerdict(
            "red",
            "🔴",
            "Подождать",
            f"озвучка молитв: running={tts.get('running')} waiting={tts.get('waiting')}",
        )

    yellow_reasons: List[str] = []
    if unique > LIVE_YELLOW_UNIQUE_USERS:
        yellow_reasons.append(
            f"уникальных юзеров за {LIVE_WINDOW_MINUTES} мин: {unique}"
            f" (порог {LIVE_YELLOW_UNIQUE_USERS})"
        )
    if msgs > LIVE_YELLOW_MESSAGES:
        yellow_reasons.append(
            f"сообщений за {LIVE_WINDOW_MINUTES} мин: {msgs}"
            f" (порог {LIVE_YELLOW_MESSAGES})"
        )
    if dialog_busy:
        yellow_reasons.append(
            f"очереди диалога: users={dialog.get('users_with_queue')}"
            f" processing={dialog.get('processing_users')}"
        )

    if yellow_reasons:
        return LiveVerdict(
            "yellow",
            "🟡",
            "Осторожно",
            "; ".join(yellow_reasons),
        )

    return LiveVerdict(
        "green",
        "🟢",
        "Можно деплоить",
        "нет running-рассылки, TTS свободен, нагрузка низкая",
    )


def format_bot_live_status_html(
    *,
    campaigns: Sequence[Dict[str, Any]],
    activity: Dict[str, int],
    dialog: Dict[str, int],
    tts: Dict[str, int],
    verdict: LiveVerdict,
) -> str:
    now_s = _msk_now().strftime("%d.%m.%Y %H:%M:%S MSK")
    running = [c for c in campaigns if (c.get("status") or "") == "running"]
    planned = [c for c in campaigns if (c.get("status") or "") == "planned"]

    parts = [
        f"{verdict.emoji} <b>{html_mod.escape(verdict.title)}</b>",
        f"<i>{html_mod.escape(verdict.reason)}</i>",
        "",
        f"<i>на сейчас · {html_mod.escape(now_s)}</i>",
        "",
        "<b>📨 Рассылки</b>",
    ]
    if not running and not planned:
        parts.append("• running / planned: нет")
    for c in running:
        open_left = int(c.get("pending_count") or 0) + int(c.get("processing_count") or 0)
        parts.append(
            f"• ▶️ <b>#{int(c.get('id') or 0)}</b> "
            f"{html_mod.escape(str(c.get('name') or ''))} — "
            f"sent {int(c.get('sent_count') or 0)}, осталось {open_left}, "
            f"аудитория {int(c.get('audience_total') or 0)}"
        )
    for c in planned[:3]:
        parts.append(
            f"• ⏳ planned <b>#{int(c.get('id') or 0)}</b> "
            f"{html_mod.escape(str(c.get('name') or ''))} — "
            f"план {_fmt_dt(c.get('scheduled_at'))}, "
            f"аудитория {int(c.get('audience_total') or 0)}"
        )

    parts.extend(
        [
            "",
            f"<b>⏱ Нагрузка за {LIVE_WINDOW_MINUTES} мин</b>",
            (
                f"• сообщений: <b>{int(activity.get('messages') or 0)}</b>"
                f" (user {int(activity.get('user_messages') or 0)}"
                f" / bot {int(activity.get('bot_messages') or 0)})"
            ),
            (
                f"• уникальных юзеров: <b>{int(activity.get('unique_users') or 0)}</b>"
                f" · порог жёлтого: {LIVE_YELLOW_UNIQUE_USERS}"
            ),
            "",
            "<b>🗂 Очереди диалога</b>",
            (
                f"• юзеров с очередью: {int(dialog.get('users_with_queue') or 0)}"
                f" · сообщений в очередях: {int(dialog.get('queued_messages') or 0)}"
            ),
            f"• сейчас обрабатывается: {int(dialog.get('processing_users') or 0)}",
            "",
            "<b>🙏 Молитвы TTS</b>",
            (
                f"• running={int(tts.get('running') or 0)}"
                f" waiting={int(tts.get('waiting') or 0)}"
                f" max={int(tts.get('max_concurrent') or 0)}"
            ),
            "",
            "<i>Команда: /status · /live</i>",
        ]
    )
    return "\n".join(parts)


async def build_bot_live_status_html(pool, bot_app: Any = None) -> str:
    if pool is None:
        return "❌ База данных недоступна."
    try:
        campaigns = await fetch_running_and_planned_campaigns(pool)
        activity = await fetch_recent_message_activity(
            pool, minutes=LIVE_WINDOW_MINUTES
        )
    except Exception as e:
        logger.exception("live status db: %s", e)
        return f"❌ Не удалось собрать статус: {html_mod.escape(str(e)[:200])}"

    dialog = snapshot_dialog_queues(bot_app)
    tts = snapshot_prayer_tts()
    verdict = decide_verdict(
        campaigns=campaigns,
        activity=activity,
        dialog=dialog,
        tts=tts,
    )
    return format_bot_live_status_html(
        campaigns=campaigns,
        activity=activity,
        dialog=dialog,
        tts=tts,
        verdict=verdict,
    )
