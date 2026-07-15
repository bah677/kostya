"""
Сервис для работы с BZB Payment API.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from config import config

from .base import PaymentProvider

logger = logging.getLogger(__name__)


class BZBCreatePaymentError(Exception):
    """Ошибка создания платежа в BZB API."""

    def __init__(self, status: int, detail: str, *, user_message: Optional[str] = None):
        self.status = status
        self.detail = detail
        self.user_message = user_message or (
            "Не удалось создать платёж. Попробуйте позже или выберите другой способ оплаты."
        )
        super().__init__(detail)


def _user_message_for_bzb_detail(detail: str) -> Optional[str]:
    low = detail.lower()
    if "recurring payments are not enabled" in low:
        return (
            "📅 **Автопродление временно недоступно**\n\n"
            "У платёжного провайдера не включены рекуррентные списания.\n\n"
            "Оформите **разовый платёж** или напишите в /support."
        )
    return None


class BZBService(PaymentProvider):
    """Сервис для работы с BZB Payment API"""

    def __init__(self):
        self.api_key = config.BZB_API_KEY
        self.base_url = (config.BZB_API_URL or "https://public-api.bezebee.com").rstrip("/")
        self.headers = {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
        }
        logger.info("✅ BZB service initialized (base_url=%s)", self.base_url)

    @property
    def provider_name(self) -> str:
        return "bzb"

    async def create_payment(
        self,
        amount: float,
        description: str,
        user_id: int,
        payment_type: str = "one_time",
        bot_username: str = None,
        **kwargs,
    ) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
        """
        Создаёт платёж или payment link (ONE_TIME / RECURRING).

        ``currency`` в kwargs: RUB → контур РФ, USD → международный (по настройкам BZB).

        Returns:
            Tuple[payment_url, payment_id, metadata]
        """
        _ = user_id
        try:
            back_url = f"https://t.me/{bot_username}" if bot_username else None
            title = kwargs.get("title", description[:50])
            currency = (kwargs.get("currency") or "USD").strip().upper()
            link_type = (kwargs.get("link_type") or "ONE_TIME").strip().upper()
            if (
                payment_type == "subscription"
                and config.SUBSCRIPTION_RECURRING_ENABLED
                and link_type != "ONE_TIME"
            ):
                link_type = "RECURRING"

            payload: Dict[str, Any] = {
                "amount": float(amount),
                "currency": currency,
                "title": title,
                "description": description[:500],
                "back_url": back_url,
                "type": link_type,
            }
            if link_type == "RECURRING":
                payload["recurring_interval_unit"] = (
                    kwargs.get("recurring_interval_unit") or "MONTH"
                )
                payload["recurring_interval_count"] = int(
                    kwargs.get("recurring_interval_count") or 1
                )

            logger.info("💰 BZB create payment: %s", payload)

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/api/v1/payments",
                    headers=self.headers,
                    json=payload,
                ) as response:
                    if response.status == 201:
                        data = await response.json()
                        payment_id = data.get("id")
                        payment_url = data.get("payment_url")
                        metadata = {
                            "short_id": data.get("short_id"),
                            "created_at": data.get("created_at"),
                            "currency": currency,
                            "link_type": link_type,
                            "raw_response": data,
                        }
                        logger.info(
                            "✅ BZB payment created: %s, url: %s", payment_id, payment_url
                        )
                        return payment_url, payment_id, metadata

                    error_text = await response.text()
                    detail = error_text
                    try:
                        err_payload = json.loads(error_text)
                        if isinstance(err_payload, dict) and err_payload.get("detail"):
                            detail = str(err_payload["detail"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                    logger.error(
                        "❌ BZB create payment failed: %s - %s",
                        response.status,
                        detail,
                    )
                    raise BZBCreatePaymentError(
                        response.status,
                        detail,
                        user_message=_user_message_for_bzb_detail(detail),
                    )

        except BZBCreatePaymentError:
            raise
        except Exception as e:
            logger.error("❌ BZB create payment error: %s", e, exc_info=True)
            return None, None, None

    async def check_payment_status(self, payment_id: str) -> Tuple[str, Dict[str, Any]]:
        """Проверяет статус payment link."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/api/v1/payments/{payment_id}",
                    headers=self.headers,
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        status_map = {
                            "pending": "pending",
                            "confirmed": "succeeded",
                            "cancelled": "canceled",
                        }
                        bzb_status = data.get("status", "pending")
                        our_status = status_map.get(bzb_status, "pending")
                        details = {
                            "id": data.get("id"),
                            "status": our_status,
                            "amount": data.get("amount"),
                            "currency": data.get("currency"),
                            "raw_status": bzb_status,
                            "raw_response": data,
                        }
                        return our_status, details

                    logger.error("❌ BZB check status failed: %s", response.status)
                    return "error", {}

        except Exception as e:
            logger.error("❌ BZB check status error: %s", e)
            return "error", {}
