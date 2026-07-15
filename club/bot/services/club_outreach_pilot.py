"""Пилотная группа для клубных рассылок в личку."""

from __future__ import annotations

import logging
from typing import List

from config import config

logger = logging.getLogger(__name__)


async def refresh_pilot_cohort(user_storage) -> List[int]:
    """Обновляет pilot_cohort: admins + топ-N активных в групповом чате."""
    gid = int(config.CLUB_GROUP_ID or 0)
    limit = int(config.CLUB_OUTREACH_PILOT_SIZE or 30)
    lookback = int(config.CLUB_OUTREACH_PILOT_LOOKBACK_DAYS or 30)

    active_top = await user_storage.fetch_top_group_active_user_ids(
        gid, limit=limit, lookback_days=lookback
    )
    admin_rows = await user_storage.list_telegram_admin_ids()
    admin_ids = [
        int(r["telegram_user_id"])
        for r in admin_rows
        if r.get("telegram_user_id") is not None
    ]

    merged: List[int] = []
    seen: set[int] = set()
    for uid in admin_ids + active_top:
        if uid not in seen:
            seen.add(uid)
            merged.append(uid)

    if not merged:
        logger.warning("pilot cohort empty (group=%s, admins=%s)", gid, len(admin_ids))
        return []

    await user_storage.set_pilot_cohort(merged, pilot=True)
    logger.info(
        "pilot cohort refreshed: %s users (%s admins + %s active top)",
        len(merged),
        len(admin_ids),
        len(active_top),
    )
    return merged


async def resolve_outreach_recipients(user_storage) -> List[int]:
    """Список получателей рассылки с учётом режима пилота."""
    if config.CLUB_OUTREACH_DM_PILOT_ONLY:
        ids = await user_storage.list_pilot_outreach_user_ids()
        if ids:
            return ids
        return await refresh_pilot_cohort(user_storage)

    return await user_storage.list_user_ids_with_active_license()
