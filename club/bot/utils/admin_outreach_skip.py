"""Пропуск маркетинговых уведомлений для admins."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from storage.user_storage import UserStorage

REFERRAL_BONUS_SLUGS = frozenset({"referral_bonus_notify"})
AFFILIATE_SLUGS = frozenset({"affiliate_and_extend", "rem_affiliate"})
BONUS_EXTENSION_SLUGS = frozenset(
    {"bonus_extension_plus_one_day", "post_bonus_expiry_final"}
)


async def should_skip_referral_bonus_dm(
    user_storage: "UserStorage", user_id: int
) -> bool:
    return await user_storage.is_telegram_admin_id(user_id)


async def should_skip_subscription_outreach_slug(
    user_storage: "UserStorage",
    user_id: int,
    slug: str,
) -> bool:
    if not await user_storage.is_telegram_admin_id(user_id):
        return False
    s = (slug or "").strip()
    if s in BONUS_EXTENSION_SLUGS:
        return True
    if s in AFFILIATE_SLUGS:
        return True
    if s.startswith("expiry_minus_") and "affiliate" in s:
        return True
    return False
