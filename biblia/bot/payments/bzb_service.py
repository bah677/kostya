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
            "📅 **Ежемесячная поддержка временно недоступна**\n\n"
            "У платёжного провайдера не включены рекуррентные списания для этого аккаунта.\n\n"
            "Пока можно оформить **разовый платёж** — или напишите в поддержку бота."
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

        Returns:
            Tuple[payment_url, payment_id, metadata]
        """
        _ = user_id
        try:
            back_url = f"https://t.me/{bot_username}" if bot_username else None
            title = kwargs.get("title", description[:50])
            currency = (kwargs.get("currency") or "USD").strip().upper()
            link_type = (kwargs.get("link_type") or "ONE_TIME").strip().upper()
            if payment_type == "subscription":
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
                        payload = json.loads(error_text)
                        if isinstance(payload, dict) and payload.get("detail"):
                            detail = str(payload["detail"])
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

    async def get_subscription(self, subscription_id: str) -> Optional[Dict[str, Any]]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/api/v1/subscriptions/{subscription_id}",
                    headers=self.headers,
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    logger.error(
                        "❌ BZB get subscription %s failed: %s",
                        subscription_id,
                        response.status,
                    )
                    return None
        except Exception as e:
            logger.error("❌ BZB get subscription error: %s", e)
            return None

    async def list_subscriptions(
        self,
        *,
        status: Optional[str] = None,
        currency: Optional[str] = None,
        offset: int = 0,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"offset": offset, "limit": limit}
        if status:
            params["status"] = status
        if currency:
            params["currency"] = currency.upper()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/api/v1/subscriptions",
                    headers=self.headers,
                    params=params,
                ) as response:
                    if response.status != 200:
                        logger.error("❌ BZB list subscriptions failed: %s", response.status)
                        return []
                    data = await response.json()
                    return list(data.get("subscriptions") or [])
        except Exception as e:
            logger.error("❌ BZB list subscriptions error: %s", e)
            return []

    async def find_subscription_by_payment_link_id(
        self, payment_link_id: str
    ) -> Optional[Dict[str, Any]]:
        """Ищет подписку по payment_link_id (пагинация по всем активным статусам)."""
        if not payment_link_id:
            return None
        for status in ("PENDING", "ACTIVE", "PAST_DUE", "CANCELED"):
            offset = 0
            while True:
                batch = await self.list_subscriptions(status=status, offset=offset, limit=100)
                if not batch:
                    break
                for sub in batch:
                    if sub.get("payment_link_id") == payment_link_id:
                        return sub
                if len(batch) < 100:
                    break
                offset += 100
        return None

    async def cancel_subscription(self, subscription_id: str) -> Optional[Dict[str, Any]]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/api/v1/subscriptions/{subscription_id}/cancel",
                    headers=self.headers,
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    error_text = await response.text()
                    logger.error(
                        "❌ BZB cancel subscription %s failed: %s %s",
                        subscription_id,
                        response.status,
                        error_text,
                    )
                    return None
        except Exception as e:
            logger.error("❌ BZB cancel subscription error: %s", e)
            return None
