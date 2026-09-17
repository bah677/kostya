"""Допуск к анкете подарочной волны (АНК-1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from bot.admin_guard import is_admin_or_super
from config import config
from storage.db.gift_application import CAMPAIGN_ID


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    reason: Optional[str] = None
    # active_license | expired_access | admin | already_applied | sold_out | paid | gift_license
    kind: Optional[str] = None
    active_license: Optional[dict] = None


async def count_remaining_tickets(user_storage, *, campaign: str = CAMPAIGN_ID) -> int:
    total = int(getattr(config, "GIFT_CAMPAIGN_TICKETS", 150) or 150)
    async with user_storage.get_connection() as conn:
        used = await conn.fetchval(
            """
            SELECT COUNT(*)::int
            FROM gift_wave_member gwm
            JOIN gift_wave gw ON gw.id = gwm.wave_id
            WHERE gwm.status IN ('granted', 'activated')
              AND (
                    gw.campaign = $1
                    OR gwm.application_id IN (
                        SELECT id FROM gift_application WHERE campaign = $1
                    )
                  )
            """,
            campaign,
        )
    return max(0, total - int(used or 0))


async def user_has_any_gift_license(user_storage, user_id: int) -> bool:
    async with user_storage.get_connection() as conn:
        row = await conn.fetchval(
            """
            SELECT 1 FROM license
            WHERE user_id = $1 AND origin = 'gift'
            LIMIT 1
            """,
            user_id,
        )
    return bool(row)


async def user_had_any_license(user_storage, user_id: int) -> bool:
    async with user_storage.get_connection() as conn:
        row = await conn.fetchval(
            """
            SELECT 1 FROM license WHERE user_id = $1 LIMIT 1
            """,
            user_id,
        )
    return bool(row)


async def check_gift_application_eligibility(
    user_storage,
    user_id: int,
    *,
    campaign: str = CAMPAIGN_ID,
    allow_existing_draft: bool = True,
) -> EligibilityResult:
    left = await count_remaining_tickets(user_storage, campaign=campaign)
    if left <= 0:
        return EligibilityResult(False, reason="sold_out", kind="sold_out")

    existing = await user_storage.get_gift_application(user_id, campaign=campaign)
    if existing:
        st = existing.get("status")
        if st in ("draft", "cancelled", "ineligible") and allow_existing_draft:
            pass  # можно продолжить / перезапустить
        elif st not in ("cancelled",):
            return EligibilityResult(
                False, reason="already_applied", kind="already_applied"
            )

    if await is_admin_or_super(user_storage, user_id):
        return EligibilityResult(False, reason="admin", kind="admin")

    paid_n = await user_storage.get_user_successful_payments_count(user_id)
    allow_trial = bool(getattr(config, "GIFT_ALLOW_TRIAL_PAYERS", False))
    if paid_n > 0 and not allow_trial:
        # если платил — не допускаем; дальше уточним сообщение по лицензии
        active = await user_storage.get_user_active_license(user_id)
        if active:
            return EligibilityResult(
                False,
                reason="paid_active",
                kind="active_license",
                active_license=active,
            )
        if await user_had_any_license(user_storage, user_id):
            return EligibilityResult(False, reason="paid_expired", kind="expired_access")
        return EligibilityResult(False, reason="paid", kind="expired_access")

    if await user_has_any_gift_license(user_storage, user_id):
        active = await user_storage.get_user_active_license(user_id)
        if active:
            return EligibilityResult(
                False,
                reason="gift_active",
                kind="active_license",
                active_license=active,
            )
        return EligibilityResult(False, reason="gift_expired", kind="expired_access")

    active = await user_storage.get_user_active_license(user_id)
    if active:
        return EligibilityResult(
            False,
            reason="active_license",
            kind="active_license",
            active_license=active,
        )

    if await user_had_any_license(user_storage, user_id):
        return EligibilityResult(False, reason="had_license", kind="expired_access")

    return EligibilityResult(True)


async def user_has_pending_gift_ticket(user_storage, user_id: int) -> Optional[dict]:
    """Выданный, но не активированный билет (status=granted)."""
    async with user_storage.get_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT gwm.*, gw.gift_days, gw.campaign, gw.title
            FROM gift_wave_member gwm
            JOIN gift_wave gw ON gw.id = gwm.wave_id
            WHERE gwm.user_id = $1
              AND gwm.status = 'granted'
            ORDER BY gwm.granted_at DESC NULLS LAST
            LIMIT 1
            """,
            user_id,
        )
    return dict(row) if row else None
