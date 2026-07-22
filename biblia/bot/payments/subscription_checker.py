"""Polling BZB subscriptions: повторные списания и синхронизация статуса."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from bot.payments.payment_conversion import compute_payment_rub_from_row
from bot.payments.standalone_donation_notify import (
    notify_admins_standalone_donation_success,
)
from bot.services.donation_marathon_attr import attribute_payment_to_marathon

logger = logging.getLogger(__name__)


def _renewal_provider_payment_id(bzb_subscription_id: str, last_charge_at: datetime) -> str:
    ts = last_charge_at.strftime("%Y%m%dT%H%M%S")
    return f"sub_renewal:{bzb_subscription_id}:{ts}"


class SubscriptionChecker:
    """Отслеживает last_charge_at у активных donation_subscriptions."""

    def __init__(
        self,
        user_storage,
        bzb_service,
        bot,
        currency_converter=None,
        check_interval: int = 900,
    ):
        self.user_storage = user_storage
        self.bzb_service = bzb_service
        self.bot = bot
        self.currency_converter = currency_converter
        self.check_interval = check_interval
        self.is_running = False
        self.check_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if not self.bzb_service:
            logger.info("SubscriptionChecker не запущен: BZB недоступна")
            return
        self.is_running = True
        self.check_task = asyncio.create_task(self._loop())
        logger.info("✅ SubscriptionChecker started (interval=%ss)", self.check_interval)

    async def stop(self) -> None:
        self.is_running = False
        if self.check_task:
            self.check_task.cancel()
            try:
                await self.check_task
            except asyncio.CancelledError:
                pass
        logger.info("✅ SubscriptionChecker stopped")

    async def _loop(self) -> None:
        while self.is_running:
            try:
                await self._check_subscriptions()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("❌ SubscriptionChecker loop error: %s", e, exc_info=True)
                await asyncio.sleep(60)

    async def _check_subscriptions(self) -> None:
        rows = await self.user_storage.list_pollable_donation_subscriptions()
        if not rows:
            return
        for row in rows:
            try:
                await self._check_single(row)
            except Exception as e:
                logger.error(
                    "❌ SubscriptionChecker sub_id=%s: %s", row.get("id"), e, exc_info=True
                )

    async def _check_single(self, local_sub: Dict[str, Any]) -> None:
        bzb_id = local_sub["bzb_subscription_id"]
        remote = await self.bzb_service.get_subscription(bzb_id)
        if not remote:
            return

        fields = self.user_storage.bzb_subscription_fields(remote)
        remote_status = fields["status"]
        remote_last = fields["last_charge_at"]
        remote_next = fields["next_charge_at"]
        remote_canceled = fields["canceled_at"]

        stored_last = local_sub.get("last_charge_at")
        if isinstance(stored_last, str):
            try:
                stored_last = datetime.fromisoformat(stored_last.replace("Z", "+00:00"))
            except ValueError:
                stored_last = None

        await self.user_storage.update_donation_subscription_from_bzb(
            int(local_sub["id"]),
            status=remote_status,
            last_charge_at=remote_last,
            next_charge_at=remote_next,
            canceled_at=remote_canceled if remote_status == "CANCELED" else None,
        )

        if remote_last and (stored_last is None or remote_last > stored_last):
            initial_pid = local_sub.get("initial_payment_id")
            if initial_pid:
                initial = await self.user_storage.get_payment(int(initial_pid))
                initial_charge = None
                if initial and initial.get("completed_at"):
                    ic = initial["completed_at"]
                    if isinstance(ic, str):
                        try:
                            initial_charge = datetime.fromisoformat(ic.replace("Z", "+00:00"))
                        except ValueError:
                            initial_charge = None
                    elif hasattr(ic, "year"):
                        initial_charge = ic
                    if (
                        initial_charge
                        and abs((remote_last - initial_charge).total_seconds()) < 120
                        and stored_last is None
                    ):
                        return

            await self._record_renewal(local_sub, remote, remote_last)

    async def _record_renewal(
        self,
        local_sub: Dict[str, Any],
        remote: Dict[str, Any],
        last_charge_at: datetime,
    ) -> None:
        bzb_id = local_sub["bzb_subscription_id"]
        provider_pid = _renewal_provider_payment_id(bzb_id, last_charge_at)
        if await self.user_storage.payment_exists_by_provider_id("bzb", provider_pid):
            return

        user_id = int(local_sub["user_id"])
        amount = float(remote.get("amount") or local_sub.get("amount") or 0)
        currency = (remote.get("currency") or local_sub.get("currency") or "RUB").upper()

        initial = None
        if local_sub.get("initial_payment_id"):
            initial = await self.user_storage.get_payment(int(local_sub["initial_payment_id"]))
        user_telegram_data = (initial or {}).get("user_telegram_data")

        row_id = await self.user_storage.create_payment(
            user_id=user_id,
            amount=amount,
            payment_type="subscription_renewal",
            provider="bzb",
            provider_payment_id=provider_pid,
            subscription_id=bzb_id,
            user_telegram_data=user_telegram_data,
            currency=currency,
            order_id=None,
        )
        if not row_id:
            logger.error("❌ renewal create_payment failed sub=%s", bzb_id)
            return

        payment_pending = await self.user_storage.get_payment(row_id)
        rub_amount, exchange_rate = await compute_payment_rub_from_row(
            self.currency_converter,
            payment_pending or {},
        )

        payment_row = None
        if rub_amount is not None and exchange_rate is not None:
            payment_row = await self.user_storage.try_finalize_standalone_payment_success(
                payment_id=row_id,
                provider_payment_id=provider_pid,
                rub_amount=rub_amount,
                exchange_rate=exchange_rate,
            )

        if payment_row is None:
            await self.user_storage.update_payment_status(
                payment_id=row_id,
                status="succeeded",
                provider_payment_id=provider_pid,
            )
            payment_row = await self.user_storage.get_payment(row_id)
            if (
                payment_row
                and payment_row.get("amount_rub") is None
                and rub_amount is not None
                and exchange_rate is not None
            ):
                await self.user_storage.update_payment_with_conversion(
                    row_id,
                    rub_amount,
                    exchange_rate,
                )
                payment_row = await self.user_storage.get_payment(row_id)

        if not payment_row:
            return

        if rub_amount is None:
            rub_amount = (
                float(payment_row["amount_rub"])
                if payment_row.get("amount_rub") is not None
                else None
            )

        marathon_thank = None
        try:
            _, marathon_thank = await attribute_payment_to_marathon(
                self.user_storage,
                payment_row,
                rub_amount=rub_amount,
                currency_converter=self.currency_converter,
            )
        except Exception as mar_e:
            logger.error(
                "❌ Марафон attribution renewal payment_id=%s: %s",
                row_id,
                mar_e,
                exc_info=True,
            )

        await notify_admins_standalone_donation_success(
            self.bot,
            self.user_storage,
            payment_row,
            rub_amount=rub_amount,
            kind="subscription_renewal",
        )

        try:
            if marathon_thank:
                thank_text = marathon_thank
            else:
                thank_text = (
                    "🙏 <b>Спасибо за ежемесячную поддержку!</b>\n\n"
                    "Списание прошло успешно. Пусть ваше пожертвование вернётся сторицей."
                )
            await self.bot.send_message(
                user_id,
                thank_text,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("renewal thank-you user=%s: %s", user_id, e)

        logger.info(
            "💰 subscription renewal recorded payment_id=%s sub=%s user=%s",
            row_id,
            bzb_id,
            user_id,
        )
