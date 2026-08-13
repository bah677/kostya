# bot/payments/payment_checker.py
"""Периодический опрос pending-платежей у агрегаторов с деградирующим интервалом."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from bot.payments.payment_provider_router import resolve_payment_service_by_name
from config import config

logger = logging.getLogger(__name__)


def poll_interval_for_age(age: timedelta) -> timedelta:
    """
    Чем старше pending-ссылка, тем реже опрашиваем.

    Сутки 1 — часто; дальше интервал растёт до 1 раза в сутки.
    """
    hours = max(0.0, age.total_seconds() / 3600.0)
    days = hours / 24.0
    if hours < 1:
        return timedelta(minutes=1)
    if hours < 6:
        return timedelta(minutes=5)
    if hours < 24:
        return timedelta(minutes=15)
    if days < 2:
        return timedelta(minutes=30)
    if days < 3:
        return timedelta(hours=1)
    if days < 5:
        return timedelta(hours=3)
    if days < 8:
        return timedelta(hours=6)
    if days < 15:
        return timedelta(hours=12)
    return timedelta(hours=24)


def _as_naive(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def payment_due_for_check(payment: Dict[str, Any], *, now: Optional[datetime] = None) -> bool:
    """True, если по возрасту ссылки пора снова спросить агрегатор."""
    now = now or datetime.now()
    created = _as_naive(payment.get("created_at"))
    if created is None:
        return True
    age = now - created
    if age < timedelta(0):
        age = timedelta(0)
    interval = poll_interval_for_age(age)
    last = _as_naive(payment.get("last_checked_at"))
    if last is None:
        return True
    return (now - last) >= interval


class PaymentChecker:
    """Сервис для периодической проверки статуса платежей."""

    def __init__(
        self,
        user_storage,
        yookassa_service,
        bzb_service,
        bot,
        currency_converter,
        order_fulfillment=None,
        payment_feature=None,
        feature_manager=None,
        check_interval: Optional[int] = None,
    ):
        self.user_storage = user_storage
        self.yookassa_service = yookassa_service
        self.bzb_service = bzb_service
        self.bot = bot
        self.currency_converter = currency_converter
        self.order_fulfillment = order_fulfillment
        self.payment_feature = payment_feature
        self.feature_manager = feature_manager
        loop_sec = (
            int(check_interval)
            if check_interval is not None
            else int(getattr(config, "PAYMENT_CHECK_LOOP_SEC", 60) or 60)
        )
        self.check_interval = max(15, loop_sec)
        self.max_age_days = max(
            3, int(getattr(config, "PAYMENT_CHECK_MAX_AGE_DAYS", 30) or 30)
        )
        self.api_pause_sec = max(
            0.0, float(getattr(config, "PAYMENT_CHECK_API_PAUSE_SEC", 0.25) or 0.0)
        )
        self.is_running = False
        self.check_task: Optional[asyncio.Task] = None

    async def start(self):
        """Запускает периодическую проверку платежей."""
        self.is_running = True
        self.check_task = asyncio.create_task(self._check_payments_loop())
        logger.info(
            "✅ Payment checker started (loop=%ss, max_age=%sd, api_pause=%.2fs)",
            self.check_interval,
            self.max_age_days,
            self.api_pause_sec,
        )

    async def stop(self):
        """Останавливает проверку платежей."""
        self.is_running = False
        if self.check_task:
            self.check_task.cancel()
            try:
                await self.check_task
            except asyncio.CancelledError:
                pass
        logger.info("✅ Payment checker stopped")

    async def _check_payments_loop(self):
        """Основной цикл проверки платежей."""
        while self.is_running:
            try:
                await self._check_pending_payments()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Error in payment check loop: {e}")
                await asyncio.sleep(30)

    async def _check_pending_payments(self):
        """Опрашивает due pending; протухшие по max_age помечает expired."""
        try:
            expired_n = await self.user_storage.expire_stale_pending_payments(
                max_age_days=self.max_age_days
            )
            if expired_n:
                logger.info(
                    "PaymentChecker: expired %s stale pending (>%sd)",
                    expired_n,
                    self.max_age_days,
                )

            all_pending = await self.user_storage.get_pending_payments_for_poll(
                max_age_days=self.max_age_days
            )
            if not all_pending:
                return

            now = datetime.now()
            due = [p for p in all_pending if payment_due_for_check(p, now=now)]
            if not due:
                return

            logger.debug(
                "PaymentChecker: pending=%s due=%s",
                len(all_pending),
                len(due),
            )

            for payment in due:
                provider = (payment.get("payment_provider") or "yookassa").strip().lower()
                try:
                    await self._check_single_payment(payment, provider)
                except Exception as e:
                    logger.error(
                        "❌ Failed to check %s payment %s: %s",
                        provider,
                        payment.get("id"),
                        e,
                    )
                if self.api_pause_sec > 0:
                    await asyncio.sleep(self.api_pause_sec)

        except Exception as e:
            logger.error(f"❌ Error checking pending payments: {e}")

    async def _check_single_payment(self, payment: Dict[str, Any], provider: str):
        """Проверяет статус одного платежа для конкретного провайдера."""
        payment_id = payment["id"]
        provider_payment_id = payment["provider_payment_id"]
        user_id = payment["user_id"]

        if not provider_payment_id:
            logger.warning(f"⚠️ No provider_payment_id for payment {payment_id}")
            return

        try:
            service, _ = resolve_payment_service_by_name(
                provider,
                yookassa_service=self.yookassa_service,
                bzb_service=self.bzb_service,
            )
        except RuntimeError as e:
            logger.warning(
                "Payment %s provider %s unavailable: %s", payment_id, provider, e
            )
            return

        try:
            status, details = await service.check_payment_status(provider_payment_id)
            await self.user_storage.touch_payment_checked_at(payment_id)

            if status == "succeeded":
                logger.info(f"💰 Payment {payment_id} ({provider}) succeeded")

                if payment.get("order_id") is None:
                    await self._finalize_standalone_payment(
                        payment_id=payment_id,
                        user_id=user_id,
                        provider_payment_id=provider_payment_id,
                    )
                    return

                if not self.order_fulfillment:
                    logger.error(
                        "❌ order_fulfillment не задан, пропуск оплаты с заказом id=%s",
                        payment_id,
                    )
                    return

                order = await self.user_storage.get_order(payment["order_id"])
                if not order:
                    logger.error(
                        f"❌ Order {payment['order_id']} not found for payment {payment_id}"
                    )
                    return

                rub_amount = await self.order_fulfillment.compute_rub_amount(
                    order, payment
                )
                if not rub_amount:
                    logger.error(f"❌ Failed to convert payment {payment_id} to RUB")
                    return

                exchange_rate = rub_amount / float(order["amount"])

                finalized = await self.order_fulfillment.finalize_pending_payment_or_none(
                    payment_id=payment_id,
                    provider_payment_id=provider_payment_id,
                    rub_amount=rub_amount,
                    exchange_rate=exchange_rate,
                )

                fresh = await self.user_storage.get_payment(payment_id)
                if not fresh or fresh.get("status") != "succeeded":
                    logger.error(
                        f"❌ Payment {payment_id} not succeeded after finalize attempt"
                    )
                    return

                await self.order_fulfillment.deliver_after_successful_payment_row(fresh)
                logger.debug(
                    "finalize outcome payment_id=%s claimed_row=%s",
                    payment_id,
                    finalized is not None,
                )

            elif status in ["canceled", "failed"]:
                logger.info(f"❌ Payment {payment_id} is {status}")
                await self.user_storage.update_payment_status(
                    payment_id=payment_id,
                    status=status,
                    provider_payment_id=provider_payment_id,
                )

        except Exception as e:
            # даже при ошибке API фиксируем попытку, чтобы не долбить тот же id каждую минуту
            try:
                await self.user_storage.touch_payment_checked_at(payment_id)
            except Exception:
                pass
            logger.error(
                f"❌ Error processing {provider} payment {payment_id}: {e}",
                exc_info=True,
            )

    async def _finalize_standalone_payment(
        self,
        *,
        payment_id: int,
        user_id: int,
        provider_payment_id: str,
    ) -> None:
        """Донаты и прочие платежи без заказа (order_id IS NULL)."""
        try:
            await self.user_storage.update_payment_status(
                payment_id=payment_id,
                status="succeeded",
                provider_payment_id=provider_payment_id,
            )
            try:
                await self.bot.send_message(
                    user_id,
                    "🙏 <b>Спасибо за поддержку проекта!</b>\n\n"
                    "Ваше пожертвование получено — пусть оно вернётся к вам сторицей.",
                    parse_mode="HTML",
                )
            except Exception as send_e:
                logger.warning(
                    "Не удалось отправить благодарность user=%s: %s", user_id, send_e
                )
        except Exception as e:
            logger.error(
                "❌ Ошибка финализации standalone payment_id=%s: %s",
                payment_id,
                e,
                exc_info=True,
            )
