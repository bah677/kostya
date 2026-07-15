"""Привязка BZB-подписки после первого успешного платежа."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


async def link_donation_subscription_after_payment(
    user_storage,
    bzb_service,
    payment_row: Dict[str, Any],
) -> Optional[int]:
    """
    После confirmed первого RECURRING payment link — сохраняем строку в donation_subscriptions.
    """
    if not bzb_service:
        return None
    if (payment_row.get("payment_type") or "") != "subscription":
        return None
    if (payment_row.get("payment_provider") or "") != "bzb":
        return None

    payment_link_id = payment_row.get("provider_payment_id")
    if not payment_link_id:
        return None

    try:
        bzb_sub = await bzb_service.find_subscription_by_payment_link_id(payment_link_id)
        if not bzb_sub:
            logger.warning(
                "BZB subscription not found for payment_link_id=%s payment_id=%s",
                payment_link_id,
                payment_row.get("id"),
            )
            return None

        fields = user_storage.bzb_subscription_fields(bzb_sub)
        sub_id = await user_storage.create_donation_subscription(
            user_id=int(payment_row["user_id"]),
            bzb_subscription_id=bzb_sub["id"],
            bzb_payment_link_id=payment_link_id,
            amount=float(payment_row.get("amount") or bzb_sub.get("amount") or 0),
            currency=(payment_row.get("currency") or bzb_sub.get("currency") or "RUB"),
            status=fields["status"],
            interval_unit=(bzb_sub.get("interval_unit") or "MONTH"),
            interval_count=int(bzb_sub.get("interval_count") or 1),
            last_charge_at=fields["last_charge_at"],
            next_charge_at=fields["next_charge_at"],
            started_at=fields["started_at"],
            initial_payment_id=int(payment_row["id"]),
        )
        if sub_id:
            logger.info(
                "✅ donation_subscriptions linked id=%s bzb=%s user=%s",
                sub_id,
                bzb_sub["id"],
                payment_row["user_id"],
            )
        return sub_id
    except Exception as e:
        logger.error(
            "❌ link_donation_subscription_after_payment payment_id=%s: %s",
            payment_row.get("id"),
            e,
            exc_info=True,
        )
        return None
