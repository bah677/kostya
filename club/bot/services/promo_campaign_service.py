"""Промо-кампании: deep link, скидка на базовые тарифы, контекст для агента."""

from __future__ import annotations

import copy
import re
from decimal import Decimal
from typing import Any, Dict, List, Optional

PROMO_CAMPAIGN_PREFIX = "promo_"
_GUID_RE = re.compile(r"^[0-9a-f]{8,64}$")


def _is_legacy_promo_tariff_start(param: str) -> bool:
    from bot.features.payment import resolve_promo_tariff_type_from_start_param

    return bool(resolve_promo_tariff_type_from_start_param(param))


def extract_promo_campaign_guid(start_param: str) -> Optional[str]:
    """Из ``promo_<guid>`` извлекает guid (не legacy promo_week / promo_test1week)."""
    param = (start_param or "").strip()
    if not param.startswith(PROMO_CAMPAIGN_PREFIX):
        return None
    if _is_legacy_promo_tariff_start(param):
        return None
    guid = param[len(PROMO_CAMPAIGN_PREFIX) :].strip().lower()
    if not guid or not _GUID_RE.match(guid):
        return None
    return guid


def is_promo_campaign_start_param(start_param: str) -> bool:
    return extract_promo_campaign_guid(start_param) is not None


def build_promo_campaign_deeplink(bot_username: str, guid: str) -> str:
    username = bot_username.lstrip("@")
    return f"https://t.me/{username}?start={PROMO_CAMPAIGN_PREFIX}{guid.strip().lower()}"


def discount_percent_value(promo: Dict[str, Any]) -> float:
    raw = promo.get("discount_percent")
    if isinstance(raw, Decimal):
        return float(raw)
    return float(raw or 0)


def apply_discount_amount(amount: float, discount_percent: float) -> int:
    if discount_percent <= 0:
        return max(1, int(round(amount)))
    discounted = amount * (1.0 - discount_percent / 100.0)
    return max(1, int(round(discounted)))


def apply_promo_to_tariffs(
    tariffs: List[Dict[str, Any]], promo: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Копия тарифов со скидкой: old_amount = базовая цена, amount = со скидкой."""
    pct = discount_percent_value(promo)
    out: List[Dict[str, Any]] = []
    for tariff in tariffs:
        t = copy.deepcopy(tariff)
        new_prices = []
        for price in t.get("prices") or []:
            p = dict(price)
            base = float(p.get("amount") or 0)
            if base > 0:
                p["old_amount"] = int(base) if base == int(base) else base
                p["amount"] = apply_discount_amount(base, pct)
            new_prices.append(p)
        t["prices"] = new_prices
        out.append(t)
    return out


async def get_active_promo_for_user(user_storage, user_id: int) -> Optional[Dict[str, Any]]:
    return await user_storage.get_active_user_promo_campaign(user_id)


async def assign_promo_from_start_param(
    user_storage, user_id: int, start_param: str
) -> Optional[Dict[str, Any]]:
    guid = extract_promo_campaign_guid(start_param)
    if not guid:
        return None
    ok = await user_storage.assign_user_promo_campaign(user_id, guid)
    if not ok:
        return None
    return await user_storage.get_active_user_promo_campaign(user_id)


async def build_promo_agent_addon(user_storage, user_id: int) -> str:
    promo = await get_active_promo_for_user(user_storage, user_id)
    if not promo:
        return ""
    pct = int(round(discount_percent_value(promo)))
    name = (promo.get("name") or "").strip()
    desc = (promo.get("description") or "").strip()
    tariffs = await user_storage.get_active_tariffs(tariff_type="base")
    discounted = apply_promo_to_tariffs(tariffs, promo)
    price_lines: List[str] = []
    for t in discounted:
        rub = next((p for p in t.get("prices") or [] if p.get("currency") == "RUB"), None)
        if rub:
            cur = int(rub["amount"])
            old = int(rub["old_amount"]) if rub.get("old_amount") else None
            if old:
                price_lines.append(f"• {t['name']}: {cur}₽ (вместо {old}₽)")
            else:
                price_lines.append(f"• {t['name']}: {cur}₽")
    prices_block = "\n".join(price_lines) if price_lines else "(цены уточни по кнопке оплаты)"
    parts = [
        "🔴 ПЕРСОНАЛЬНАЯ ПРОМО-АКЦИЯ (только для этого пользователя)",
        f"Название: {name}",
    ]
    if desc:
        parts.append(f"Описание: {desc}")
    parts.extend(
        [
            f"Скидка: {pct}% от базовых тарифов — **один раз**, на **одну** оплату (первую после перехода по ссылке).",
            "После этой оплаты акция сгорает; продления и следующие платежи — по обычным ценам из описания клуба.",
            "Цены со скидкой для этого пользователя **сейчас** (только до первой оплаты):",
            prices_block,
            "Как говорить об акции:",
            f"• Скидка {pct}% действует **один раз** — не «навсегда», не «цена зафиксирована», не «закрепим навсегда».",
            "• **ЗАПРЕЩЕНО** писать, что цена фиксируется навсегда / навсегда останется такой / если оформишь сейчас — цена навсегда.",
            "• В описании клуба есть правило «зафиксировать цену месяца навсегда» для обычной подписки — "
            "**при этой персональной акции оно НЕ действует**. Акция по ссылке — разовая скидка, не вечная цена.",
            "• Имеет смысл мягко подсказать: выгоднее взять **сразу несколько месяцев** (3 / 6 / 12) — процент та же, "
            "а экономия в рублях больше, потому что скидка на весь срок пакета за один платёж.",
            "• Можно предложить месяц как минимальный вход, но не скрывай, что пакет на дольше выгоднее при этой разовой скидке.",
            "• После оплаты со скидкой следующие продления — по обычным ценам из описания клуба (без этой акции).",
            "Упоминай эту акцию и персональные цены ТОЛЬКО этому пользователю.",
            "Другим пользователям без такого блока — не предлагай и не упоминай эту акцию.",
            "Призыв к оплате — маркер <<<CTA_SUBSCRIBE>>> (кнопка покажет актуальные цены).",
        ]
    )
    return "\n".join(parts)
