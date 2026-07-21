"""Форматирование прогресса и конвертация взносов марафона в валюту цели."""

from __future__ import annotations

import html
from typing import Any, Dict, Optional, Tuple


_CURRENCY_SYMBOL = {"RUB": "₽", "USD": "$", "EUR": "€"}


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


def marathon_progress_line(
    *,
    raised: float,
    goal: float,
    currency: str,
    donors: int,
) -> str:
    """Одна HTML-строка прогресса: собрано / цель / осталось / участники."""
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
        raised=raised, goal=goal, currency=cur, donors=donors
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
    goal = float(marathon.get("goal_amount") or 0)
    cur = str(marathon.get("goal_currency") or "USD").upper()
    name = html.escape(str(marathon.get("name") or "марафон"))
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
