"""Выбор провайдера доната по валюте (env: DONATION_PROVIDER_*)."""

from __future__ import annotations

from typing import Any, Optional, Tuple

from config import config


def donation_provider_for_currency(currency: str) -> str:
    cur = (currency or "").strip().upper()
    if cur == "RUB":
        return (config.DONATION_PROVIDER_RUB or "bzb").strip().lower()
    if cur == "USD":
        return (config.DONATION_PROVIDER_USD or "bzb").strip().lower()
    if cur == "EUR":
        return (config.DONATION_PROVIDER_EUR or "bzb").strip().lower()
    raise ValueError(f"Unsupported donation currency: {currency!r}")


def resolve_donation_payment_service(
    currency: str,
    *,
    yookassa_service: Any,
    bzb_service: Any,
) -> Tuple[Any, str]:
    provider = donation_provider_for_currency(currency)
    if provider == "yookassa":
        if yookassa_service is None or not config.has_yookassa:
            raise RuntimeError("YooKassa не настроена")
        return yookassa_service, "yookassa"
    if provider == "bzb":
        if bzb_service is None:
            raise RuntimeError("BZB не настроена")
        return bzb_service, "bzb"
    raise RuntimeError(f"Unknown donation provider: {provider}")
