"""Общая проверка: не выставлять из группы того, кому вызвались помочь.

Механизмов исключения два, и оба должны уважать отсрочку:

* плановый — `subscription_reminder._post_bonus_remove_one`, на следующий день
  после окончания доступа (reason `bonus_expired`);
* ночной аудит — `club_group._run_nightly_maintenance` в 21:00 UTC, он
  подчищает тех, кто остался в чате без действующей подписки дольше
  CLUB_GROUP_EXPIRED_LICENSE_GRACE_DAYS (reason `nightly_audit`).

Если учесть отсрочку только в первом, второй её отменит: его порог (3 дня)
короче отсрочки (7 дней), и он доберётся до человека раньше. Поэтому решение
живёт здесь, а оба механизма спрашивают одну и ту же функцию.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from config import config

logger = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")

#: Статусы просьбы, при которых человека ещё ждут.
LIVE_STATUSES = ("taken", "done_pending", "open", "pending_moderation")


def grace_days() -> int:
    return int(getattr(config, "WISH_BOARD_KICK_GRACE_DAYS", 7) or 0)


async def wish_holding_removal(
    user_storage: Any,
    user_id: int,
    *,
    expires_at: Optional[datetime] = None,
    today_msk: Optional[date] = None,
) -> Optional[Dict[str, Any]]:
    """Просьба, из-за которой исключение откладывается, либо None.

    expires_at — дата окончания доступа; от неё считается отсрочка. Если не
    передана, отсрочка считается действующей (вызывающий не знает срока —
    значит, судить о просрочке не может).
    """
    grace = grace_days()
    if grace <= 0:
        return None

    try:
        wish = await user_storage.wish_active_for_requester(int(user_id))
    except Exception as e:
        logger.warning("wish hold lookup uid=%s: %s", user_id, e)
        return None
    if not isinstance(wish, dict):
        return None

    # Отсрочка — исключение из общего правила, поэтому включаем её только на
    # заведомо валидной просьбе: кривые данные не должны оставлять человека
    # в группе навсегда.
    try:
        int(wish.get("id"))
    except (TypeError, ValueError):
        return None
    if str(wish.get("status") or "") not in LIVE_STATUSES:
        return None

    if isinstance(expires_at, datetime):
        exp = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        exp_date = exp.astimezone(MSK).date()
        today = today_msk or datetime.now(MSK).date()
        overdue = (today - exp_date).days
        if overdue > grace:
            logger.info(
                "wish hold: отсрочка исчерпана uid=%s wish=%s (просрочка %s дн.)",
                user_id,
                wish.get("id"),
                overdue,
            )
            return None
        wish = dict(wish)
        wish["_days_left"] = max(0, grace - overdue)
    return wish


def days_left(wish: Dict[str, Any]) -> int:
    return int(wish.get("_days_left") or grace_days())
