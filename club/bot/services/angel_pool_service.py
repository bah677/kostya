"""Расчёт слотов и weighted random для ангельских взносов."""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

MIN_AMOUNT_RUB = 7000
MIN_AMOUNT_USD = 100

PRESET_AMOUNTS_RUB = (7000, 14000, 35000)
PRESET_AMOUNTS_USD = (100, 200, 500)

# Для текста: эквивалент € (~100 USD).
MIN_AMOUNT_EUR_HINT = 100


@dataclass(frozen=True)
class AngelPoolCandidate:
    user_id: int
    expires_at: Any = None
    license_type: str = ""


def parse_donation_amount(text: str) -> Optional[float]:
    """Парсит сумму из сообщения пользователя."""
    raw = (text or "").strip()
    if not raw:
        return None
    cleaned = re.sub(r"[^\d.,]", "", raw.replace("\u00a0", " "))
    cleaned = cleaned.replace(",", ".")
    if not cleaned or cleaned == ".":
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value <= 0 or not math.isfinite(value):
        return None
    return value


def min_amount_for_currency(currency: str) -> float:
    cur = (currency or "").strip().upper()
    if cur == "RUB":
        return float(MIN_AMOUNT_RUB)
    return float(MIN_AMOUNT_USD)


def preset_amounts_for_currency(currency: str) -> tuple[int, ...]:
    cur = (currency or "").strip().upper()
    if cur == "RUB":
        return PRESET_AMOUNTS_RUB
    return PRESET_AMOUNTS_USD


def monthly_price_from_tariff(tariff: Dict[str, Any], currency: str) -> Optional[float]:
    cur = (currency or "").strip().upper()
    for p in tariff.get("prices") or []:
        if (p.get("currency") or "").upper() == cur:
            amt = p.get("amount")
            if amt is not None and float(amt) > 0:
                return float(amt)
    return None


def compute_extension_slots(amount: float, monthly_price: float) -> int:
    if monthly_price <= 0:
        return 0
    return max(1, math.ceil(float(amount) / float(monthly_price)))


def pick_monthly_base_tariff(tariffs: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    base = [t for t in tariffs if (t.get("type") or "base") == "base"]
    if not base:
        base = list(tariffs)
    if not base:
        return None
    return min(base, key=lambda t: int(t.get("duration_days") or 9999))


def pick_angel_pool_winners(
    candidates: Sequence[AngelPoolCandidate],
    slots: int,
    prior_wins: Mapping[int, int],
    *,
    rng: Optional[random.Random] = None,
) -> List[int]:
    """
    Weighted random без повторов в рамках одного взноса.
    Чем больше прошлых ангельских продлений у участника — тем ниже вес.
    """
    if slots <= 0 or not candidates:
        return []

    rnd = rng if rng is not None else random.Random()
    pool: List[AngelPoolCandidate] = list(candidates)
    rnd.shuffle(pool)

    winners: List[int] = []
    draws = min(slots, len(pool))
    for _ in range(draws):
        weights = [1.0 / (1 + int(prior_wins.get(c.user_id, 0))) for c in pool]
        total = sum(weights)
        if total <= 0:
            break
        pick = rnd.choices(range(len(pool)), weights=weights, k=1)[0]
        winner = pool.pop(pick)
        winners.append(winner.user_id)
    return winners


def candidates_from_rows(rows: Sequence[Dict[str, Any]]) -> List[AngelPoolCandidate]:
    return [
        AngelPoolCandidate(
            user_id=int(r["user_id"]),
            expires_at=r.get("expires_at"),
            license_type=str(r.get("license_type") or ""),
        )
        for r in rows
    ]
