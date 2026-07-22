# bot/payments/payment_checker.py
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from bot.payments.donation_subscription_link import link_donation_subscription_after_payment
from bot.payments.payment_conversion import compute_payment_rub_from_row
from bot.payments.standalone_donation_notify import (
    notify_admins_standalone_donation_success,
    send_donation_club_promo_message,
)
from bot.services.donation_marathon_attr import attribute_payment_to_marathon
from bot.utils.admin_channel import send_admin_html_message
from bot.services.donation_marathon_progress import format_money

logger = logging.getLogger(__name__)


class PaymentChecker:
    """Сервис для периодической проверки статуса платежей"""

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
        check_interval: int = 300,
    ):
        self.user_storage = user_storage
        self.yookassa_service = yookassa_service
        self.bzb_service = bzb_service
        self.bot = bot
        self.currency_converter = currency_converter
        self.order_fulfillment = order_fulfillment
        self.payment_feature = payment_feature
        self.feature_manager = feature_manager
        self.check_interval = check_interval
        self.is_running = False
        self.check_task: asyncio.Task = None

    async def start(self):
        """Запускает периодическую проверку платежей"""
        self.is_running = True
        self.check_task = asyncio.create_task(self._check_payments_loop())
        logger.info("✅ Payment checker started")

    async def stop(self):
        """Останавливает проверку платежей"""
        self.is_running = False
        if self.check_task:
            self.check_task.cancel()
            try:
                await self.check_task
            except asyncio.CancelledError:
                pass
        logger.info("✅ Payment checker stopped")

    async def _check_payments_loop(self):
        """Основной цикл проверки платежей"""
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
        """Проверяет все ожидающие платежи по всем провайдерам"""
        try:
            three_days_ago = datetime.now() - timedelta(days=3)
            all_pending = await self.user_storage.get_pending_payments(three_days_ago)

            if not all_pending:
                return

            yookassa_payments = [p for p in all_pending if p.get("payment_provider") == "yookassa"]
            bzb_payments = [p for p in all_pending if p.get("payment_provider") == "bzb"]

            for payment in yookassa_payments:
                try:
                    await self._check_single_payment(payment, "yookassa")
                except Exception as e:
                    logger.error(f"❌ Failed to check YooKassa payment {payment['id']}: {e}")

            for payment in bzb_payments:
                try:
                    await self._check_single_payment(payment, "bzb")
                except Exception as e:
                    logger.error(f"❌ Failed to check BZB payment {payment['id']}: {e}")

        except Exception as e:
            logger.error(f"❌ Error checking pending payments: {e}")

    async def _check_single_payment(self, payment: Dict[str, Any], provider: str):
        """Проверяет статус одного платежа для конкретного провайдера"""
        payment_id = payment["id"]
        provider_payment_id = payment["provider_payment_id"]
        user_id = payment["user_id"]

        if not provider_payment_id:
            logger.warning(f"⚠️ No provider_payment_id for payment {payment_id}")
            return

        try:
            if provider == "yookassa":
                service = self.yookassa_service
            elif provider == "bzb":
                if not self.bzb_service:
                    logger.warning(f"⚠️ BZB service not available for payment {payment_id}")
                    return
                service = self.bzb_service
            else:
                logger.error(f"❌ Unknown provider: {provider}")
                return

            status, details = await service.check_payment_status(provider_payment_id)

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
                    logger.error(f"❌ Order {payment['order_id']} not found for payment {payment_id}")
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
                    logger.error(f"❌ Payment {payment_id} not succeeded after finalize attempt")
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
            logger.error(f"❌ Error processing {provider} payment {payment_id}: {e}", exc_info=True)

    async def _finalize_standalone_payment(
        self,
        *,
        payment_id: int,
        user_id: int,
        provider_payment_id: str,
    ) -> None:
        """Донаты и прочие платежи без заказа (order_id IS NULL)."""
        try:
            pending = await self.user_storage.get_payment(payment_id)
            if not pending:
                logger.error("❌ standalone payment_id=%s не найден", payment_id)
                return

            rub_amount, exchange_rate = await compute_payment_rub_from_row(
                getattr(self, "currency_converter", None),
                pending,
            )

            payment_row: Optional[Dict[str, Any]] = None
            if pending.get("status") == "pending":
                if rub_amount is not None and exchange_rate is not None:
                    payment_row = await self.user_storage.try_finalize_standalone_payment_success(
                        payment_id=payment_id,
                        provider_payment_id=provider_payment_id,
                        rub_amount=rub_amount,
                        exchange_rate=exchange_rate,
                    )
                if payment_row is None:
                    await self.user_storage.update_payment_status(
                        payment_id=payment_id,
                        status="succeeded",
                        provider_payment_id=provider_payment_id,
                    )
                    if rub_amount is None:
                        logger.warning(
                            "FX не рассчитан для standalone payment_id=%s, amount_rub не записан",
                            payment_id,
                        )
            else:
                payment_row = pending

            payment_row = payment_row or await self.user_storage.get_payment(payment_id)
            if (
                payment_row
                and payment_row.get("amount_rub") is None
                and rub_amount is not None
                and exchange_rate is not None
            ):
                await self.user_storage.update_payment_with_conversion(
                    payment_id,
                    rub_amount,
                    exchange_rate,
                )
                payment_row = await self.user_storage.get_payment(payment_id)

            marathon_thank: Optional[str] = None
            marathon_row = None
            try:
                if payment_row and payment_row.get("order_id") is None:
                    marathon_row, marathon_thank = await attribute_payment_to_marathon(
                        self.user_storage,
                        payment_row,
                        rub_amount=rub_amount,
                        currency_converter=getattr(self, "currency_converter", None),
                    )
            except Exception as mar_e:
                logger.error(
                    "❌ Марафон attribution payment_id=%s: %s",
                    payment_id,
                    mar_e,
                    exc_info=True,
                )

            try:
                if payment_row:
                    if rub_amount is None and payment_row.get("amount_rub") is not None:
                        rub_amount = float(payment_row["amount_rub"])
                    notify_kind = "donation"
                    if (payment_row.get("payment_type") or "") == "subscription":
                        notify_kind = "subscription_initial"
                    await notify_admins_standalone_donation_success(
                        self.bot,
                        self.user_storage,
                        payment_row,
                        rub_amount=rub_amount,
                        kind=notify_kind,
                    )
            except Exception as adm_e:
                logger.error(
                    "❌ Админ-уведомление о standalone-платеже id=%s: %s",
                    payment_id,
                    adm_e,
                    exc_info=True,
                )

            try:
                if (
                    marathon_row
                    and marathon_row.get("status") == "completed"
                    and marathon_row.get("close_reason") == "goal_reached"
                ):
                    raised = await self.user_storage.get_marathon_raised_amount(
                        int(marathon_row["id"])
                    )
                    await send_admin_html_message(
                        self.bot,
                        f"🎉 Марафон <b>{marathon_row['name']}</b> завершён по цели! "
                        f"Собрано {format_money(raised, marathon_row['goal_currency'])}.",
                    )
            except Exception as mar_close_e:
                logger.error(
                    "❌ Марафон close notify payment_id=%s: %s",
                    payment_id,
                    mar_close_e,
                    exc_info=True,
                )

            try:
                if marathon_thank:
                    thank_text = marathon_thank
                elif (payment_row or {}).get("payment_type") == "subscription":
                    thank_text = (
                        "🙏 <b>Спасибо за ежемесячную поддержку!</b>\n\n"
                        "Первое списание получено. Подписка активна — "
                        "отменить можно в меню «Моя подписка» (/payment)."
                    )
                else:
                    thank_text = (
                        "🙏 <b>Спасибо за поддержку проекта!</b>\n\n"
                        "Ваше пожертвование получено — пусть оно вернётся к вам сторицей."
                    )
                await self.bot.send_message(
                    user_id,
                    thank_text,
                    parse_mode="HTML",
                )
            except Exception as send_e:
                logger.warning(
                    "Не удалось отправить благодарность user=%s: %s", user_id, send_e
                )
            if payment_row and (payment_row.get("payment_type") or "") == "subscription":
                await link_donation_subscription_after_payment(
                    self.user_storage,
                    self.bzb_service,
                    payment_row,
                )
            elif not marathon_thank:
                await send_donation_club_promo_message(self.bot, user_id)
        except Exception as e:
            logger.error(
                "❌ Ошибка финализации standalone payment_id=%s: %s",
                payment_id,
                e,
                exc_info=True,
            )
