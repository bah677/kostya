"""Баллы заявки (АНК-4)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from bot.services.biblia_club_campaign_report import (
    biblia_db_configured,
    create_biblia_pool,
)
from config import config
from storage.db.gift_application import CAMPAIGN_ID

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")


async def _club_distinct_user_days(user_storage, user_id: int) -> int:
    async with user_storage.get_connection() as conn:
        n = await conn.fetchval(
            """
            SELECT COUNT(DISTINCT (m.created_at AT TIME ZONE 'Europe/Moscow')::date)::int
            FROM messages m
            WHERE m.user_id = $1
              AND m.sender_type = 'user'
              AND COALESCE(m.chat_type, 'private') = 'private'
            """,
            user_id,
        )
    return int(n or 0)


async def _club_first_user_day(user_storage, user_id: int) -> Optional[datetime]:
    async with user_storage.get_connection() as conn:
        row = await conn.fetchval(
            """
            SELECT MIN(m.created_at)
            FROM messages m
            WHERE m.user_id = $1
              AND m.sender_type = 'user'
              AND COALESCE(m.chat_type, 'private') = 'private'
            """,
            user_id,
        )
    return row


async def _bible_signals(user_id: int) -> Dict[str, Any]:
    if not biblia_db_configured(config):
        return {"available": False, "donor": False, "prayers": 0}
    pool = None
    try:
        pool = await create_biblia_pool(config)
        async with pool.acquire() as conn:
            donor = await conn.fetchval(
                """
                SELECT 1 FROM payments
                WHERE user_id = $1 AND status = 'succeeded'
                LIMIT 1
                """,
                user_id,
            )
            prayers = await conn.fetchval(
                """
                SELECT COUNT(*)::int FROM token_usage
                WHERE user_id = $1
                  AND request_kind LIKE 'personal_prayer_compose%'
                """,
                user_id,
            )
        return {
            "available": True,
            "donor": bool(donor),
            "prayers": int(prayers or 0),
        }
    except Exception as e:
        logger.warning("bible signals uid=%s: %s", user_id, e)
        return {"available": False, "donor": False, "prayers": 0}
    finally:
        if pool is not None:
            await pool.close()


def _same_calendar_day(a: datetime, b: datetime) -> bool:
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return a.astimezone(MSK).date() == b.astimezone(MSK).date()


async def score_gift_application(
    *,
    user_storage,
    application_id: int,
) -> Dict[str, Any]:
    app = await user_storage.get_gift_application_by_id(application_id)
    if not app:
        return {"ok": False, "reason": "missing"}
    if app.get("verdict") != "pass":
        return {"ok": False, "reason": "not_pass"}

    uid = int(app["user_id"])
    parts: Dict[str, Any] = {}
    score = 0

    days = await _club_distinct_user_days(user_storage, uid)
    if days >= 2:
        score += 3
        parts["multi_day"] = 3
    else:
        parts["multi_day"] = 0

    bible = await _bible_signals(uid)
    if not bible.get("available"):
        parts["bible"] = "unavailable"
        parts["bible_donor"] = 0
        parts["bible_prayers"] = 0
    else:
        if bible.get("donor"):
            score += 3
            parts["bible_donor"] = 3
        else:
            parts["bible_donor"] = 0
        if int(bible.get("prayers") or 0) >= 2:
            score += 1
            parts["bible_prayers"] = 1
        else:
            parts["bible_prayers"] = 0

    source = (app.get("source") or "").strip()
    if source == "tg":
        score += 1
        parts["source_tg"] = 1
    else:
        parts["source_tg"] = 0

    submitted = app.get("submitted_at") or app.get("started_at")
    first_day = await _club_first_user_day(user_storage, uid)
    if submitted and first_day and _same_calendar_day(submitted, first_day):
        score -= 2
        parts["same_day_submit"] = -2
    else:
        parts["same_day_submit"] = 0

    if app.get("q3_ready") is True:
        score += 2
        parts["ready_yes"] = 2
    else:
        parts["ready_yes"] = 0

    q2 = (app.get("q2_why") or "").strip()
    if len(q2) > 100:
        score += 1
        parts["q2_long"] = 1
    else:
        parts["q2_long"] = 0

    parts["total"] = score

    await user_storage.set_gift_application_verdict(
        application_id,
        verdict="pass",
        reason=app.get("verdict_reason"),
        score=score,
        score_parts=parts,
        status="queued",
    )

    try:
        await user_storage.log_interaction(
            user_id=uid,
            event_category="gift_application",
            event_type="gift_app_scored",
            data={"application_id": application_id, "score": score, "parts": parts},
            source="gift_application",
            outcome="success",
        )
    except Exception:
        pass

    return {"ok": True, "score": score, "parts": parts}
