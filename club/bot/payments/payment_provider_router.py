"""Выбор платёжного провайдера по валюте (env: PAYMENT_PROVIDER_*)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from config import config


def payment_provider_for_currency(currency: str) -> str:
    """Имя провайдера для RUB / USD (one-time подписка и подарки)."""
    cur = (currency or "").strip().upper()
    if cur == "RUB":
        return (config.PAYMENT_PROVIDER_RUB or "yookassa").strip().lower()
    if cur == "USD":
        return (config.PAYMENT_PROVIDER_USD or "bzb").strip().lower()
    raise ValueError(f"Unsupported payment currency: {currency!r}")


def subscription_recurring_enabled_for_tariff(
    tariff: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Можно ли сохранять способ оплаты / включать рекуррент.

    Сейчас — только env ``SUBSCRIPTION_RECURRING_ENABLED``.
    Задел: позже проверять поле тарифа (например ``tariff['recurring_allowed']``).
    """
    if not config.SUBSCRIPTION_RECURRING_ENABLED:
        return False
    if tariff is not None:
        # Будущее: if tariff.get("recurring_allowed") is False: return False
        pass
    return True


def resolve_payment_service(
    currency: str,
    *,
    yookassa_service: Any,
    bzb_service: Any,
) -> Tuple[Any, str]:
    """Сервис и код провайдера для создания платежа."""
    provider = payment_provider_for_currency(currency)
    return resolve_payment_service_by_name(
        provider,
        yookassa_service=yookassa_service,
        bzb_service=bzb_service,
    )


def resolve_payment_service_by_name(
    provider: str,
    *,
    yookassa_service: Any,
    bzb_service: Any,
) -> Tuple[Any, str]:
    """Сервис по сохранённому ``payment_provider`` (yookassa / bzb)."""
    name = (provider or "").strip().lower()
    if name == "yookassa":
        if yookassa_service is None or not config.has_yookassa:
            raise RuntimeError("YooKassa не настроена")
        return yookassa_service, "yookassa"
    if name == "bzb":
        if bzb_service is None:
            raise RuntimeError("BZB не настроена")
        return bzb_service, "bzb"
    raise RuntimeError(f"Unknown payment provider: {provider!r}")
