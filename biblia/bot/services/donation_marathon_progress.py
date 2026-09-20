"""Форматирование прогресса и конвертация взносов марафона в валюту цели."""

from __future__ import annotations

import html
import math
from typing import Any, Dict, Optional, Tuple

from bot.services.prayer_voice_quota import slots_from_donation_usd

_CURRENCY_SYMBOL = {"RUB": "₽", "USD": "$", "EUR": "€"}

PROGRESS_DISPLAY_MONEY = "money"
PROGRESS_DISPLAY_PRAYERS = "prayers"


def currency_symbol(currency: str) -> str:
    cur = (currency or "").upper()
    return _CURRENCY_SYMBOL.get(cur, cur)


def format_money(amount: float, currency: str) -> str:
    cur = (currency or "").upper()
    sym = currency_symbol(cur)
    if abs(amount - round(amount)) < 0.005:
        return f"{int(round(amount))} {sym}"
    return f"{amount:.2f} {sym}"


def progress_bar(raised: float, goal: float, *, width: int = 10) -> str:
    if goal <= 0:
        pct = 0.0
    else:
        pct = max(0.0, min(1.0, raised / goal))
    filled = int(round(pct * width))
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def is_prayers_display(marathon: Optional[Dict[str, Any]]) -> bool:
    if not marathon:
        return False
    return str(marathon.get("progress_display") or "").lower() == PROGRESS_DISPLAY_PRAYERS


def _currency_is_usd_like(currency: str) -> bool:
    return (currency or "").upper() in ("USD", "USDT", "USDTTRC20", "USDT_TRC20")


def money_amount_to_prayers(amount: float, currency: str) -> int:
    """Сколько озвучек даёт сумма в валюте цели (та же формула, что у пула голоса)."""
    amt = max(0.0, float(amount or 0))
    if amt <= 0:
        return 0
    if _currency_is_usd_like(currency):
        return slots_from_donation_usd(amt)
    # Для RUB/EUR без FX в sync-форматтере: грубо через пропорциональность не здесь.
    # Вызывающий должен передать USD или использовать goal_prayers + долю.
    return slots_from_donation_usd(amt)


def resolve_goal_prayers(marathon: Dict[str, Any]) -> int:
    explicit = marathon.get("goal_prayers")
    if explicit is not None:
        try:
            n = int(explicit)
            if n > 0:
                return n
        except (TypeError, ValueError):
            pass
    goal = float(marathon.get("goal_amount") or 0)
    cur = str(marathon.get("goal_currency") or "USD")
    if goal <= 0:
        return 0
    if _currency_is_usd_like(cur):
        return max(1, slots_from_donation_usd(goal))
    # Fallback: 1 «единица цели» ≈ 1 молитва только если иначе нечего показать
    return max(1, int(math.floor(goal)))


def raised_to_prayers(
    marathon: Dict[str, Any],
    *,
    raised: float,
) -> int:
    goal = float(marathon.get("goal_amount") or 0)
    cur = str(marathon.get("goal_currency") or "USD")
    goal_p = resolve_goal_prayers(marathon)
    if goal_p <= 0:
        return 0
    if _currency_is_usd_like(cur):
        return min(goal_p, slots_from_donation_usd(float(raised or 0)))
    if goal <= 0:
        return 0
    # Пропорция к денежной цели (RUB/EUR), округление вниз.
    return min(
        goal_p,
        int(math.floor(float(raised or 0) / goal * goal_p + 1e-12)),
    )


def format_prayers_count(n: int) -> str:
    n = int(n)
    abs_n = abs(n)
    mod10 = abs_n % 10
    mod100 = abs_n % 100
    if mod10 == 1 and mod100 != 11:
        word = "молитва"
    elif 2 <= mod10 <= 4 and not (12 <= mod100 <= 14):
        word = "молитвы"
    else:
        word = "молитв"
    return f"{n} {word}"


def marathon_progress_line(
    *,
    raised: float,
    goal: float,
    currency: str,
    donors: int,
    marathon: Optional[Dict[str, Any]] = None,
) -> str:
    """Одна HTML-строка прогресса: собрано / цель / осталось / участники."""
    if marathon is not None and is_prayers_display(marathon):
        raised_p = raised_to_prayers(marathon, raised=raised)
        goal_p = resolve_goal_prayers(marathon)
        remaining_p = max(0, goal_p - raised_p)
        pct = 0 if goal_p <= 0 else min(100, int(round(100.0 * raised_p / goal_p)))
        bar = progress_bar(float(raised_p), float(goal_p))
        return (
            f"<code>{bar}</code> <b>{pct}%</b> · "
            f"собрано <b>{raised_p}</b> "
            f"из <b>{goal_p}</b> молитв · "
            f"осталось <b>{remaining_p}</b> · "
            f"участников: <b>{donors}</b>"
        )

    cur = (currency or "USD").upper()
    remaining = max(0.0, float(goal) - float(raised))
    pct = 0 if goal <= 0 else min(100, int(round(100.0 * raised / goal)))
    bar = progress_bar(raised, goal)
    return (
        f"<code>{bar}</code> <b>{pct}%</b> · "
        f"собрано <b>{html.escape(format_money(raised, cur))}</b> "
        f"из {html.escape(format_money(goal, cur))} · "
        f"осталось <b>{html.escape(format_money(remaining, cur))}</b> · "
        f"участников: <b>{donors}</b>"
    )


async def marathon_admin_notify_block(user_storage) -> str:
    """Блок прогресса активного марафона для админ-уведомлений о платежах."""
    marathon = await user_storage.get_active_donation_marathon()
    if not marathon:
        return ""
    mid = int(marathon["id"])
    raised = await user_storage.get_marathon_raised_amount(mid)
    donors = await user_storage.get_marathon_donors_count(mid)
    goal = float(marathon.get("goal_amount") or 0)
    cur = str(marathon.get("goal_currency") or "USD")
    name = html.escape(str(marathon.get("name") or "Марафон"))
    line = marathon_progress_line(
        raised=raised, goal=goal, currency=cur, donors=donors, marathon=marathon
    )
    return f"\n\n🎙️ <b>Марафон «{name}»</b>\n{line}"


def marathon_progress_html(
    marathon: Dict[str, Any],
    *,
    raised: float,
    donors: int,
) -> str:
    """Текст марафона: HTML-описание + в конце строка прогресса."""
    goal = float(marathon.get("goal_amount") or 0)
    cur = str(marathon.get("goal_currency") or "USD").upper()
    body = (marathon.get("description_html") or "").strip()
    progress = marathon_progress_line(
        raised=raised, goal=goal, currency=cur, donors=donors, marathon=marathon
    )
    if body:
        return f"{body}\n\n{progress}"
    name = html.escape(str(marathon.get("name") or "Марафон"))
    return f"<b>{name}</b>\n\n{progress}"


def payment_amount_in_goal_currency(
    *,
    payment_amount: float,
    payment_currency: str,
    goal_currency: str,
    amount_rub: Optional[float],
    rub_per_goal_unit: Optional[float],
) -> Optional[float]:
    """Deprecated shim — логика в ``donation_marathon_fx``."""
    from bot.services.donation_marathon_fx import payment_amount_in_goal_currency as _impl

    return _impl(
        payment_amount=payment_amount,
        payment_currency=payment_currency,
        goal_currency=goal_currency,
        amount_rub=amount_rub,
        rub_per_goal_unit=rub_per_goal_unit,
    )


def remaining_after_raise(goal: float, raised: float) -> float:
    return max(0.0, float(goal) - float(raised))


def thank_you_remaining_html(
    marathon: Dict[str, Any],
    *,
    raised: float,
) -> str:
    name = html.escape(str(marathon.get("name") or "марафон"))
    if is_prayers_display(marathon):
        goal_p = resolve_goal_prayers(marathon)
        raised_p = raised_to_prayers(marathon, raised=raised)
        left = max(0, goal_p - raised_p)
        if left <= 0:
            return (
                f"🙏 <b>Спасибо за поддержку!</b>\n\n"
                f"Ваш вклад в «{name}» учтён. "
                f"Цель <b>{goal_p} молитв</b> достигнута! 🎉"
            )
        return (
            f"🙏 <b>Спасибо за поддержку!</b>\n\n"
            f"Ваш вклад в «{name}» учтён.\n"
            f"До завершения сбора осталось: "
            f"<b>{html.escape(format_prayers_count(left))}</b>."
        )

    goal = float(marathon.get("goal_amount") or 0)
    cur = str(marathon.get("goal_currency") or "USD").upper()
    left = remaining_after_raise(goal, raised)
    if left <= 0:
        return (
            f"🙏 <b>Спасибо за поддержку!</b>\n\n"
            f"Ваш вклад в «{name}» учтён. "
            f"Цель <b>{html.escape(format_money(goal, cur))}</b> достигнута! 🎉"
        )
    return (
        f"🙏 <b>Спасибо за поддержку!</b>\n\n"
        f"Ваш вклад в «{name}» учтён.\n"
        f"До завершения сбора осталось: "
        f"<b>{html.escape(format_money(left, cur))}</b>."
    )


def accept_flags(marathon: Dict[str, Any]) -> Tuple[bool, bool, bool]:
    return (
        bool(marathon.get("accept_rub")),
        bool(marathon.get("accept_usd")),
        bool(marathon.get("accept_crypto")),
    )
