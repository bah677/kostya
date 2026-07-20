"""Заголовки и текст админ-уведомления об успешной оплате подписки."""

from __future__ import annotations

import html as html_mod
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from config import config, russian_days_phrase
from bot.services.attribution_touch import format_touch_key_html

if TYPE_CHECKING:
    from storage.user_storage import UserStorage

logger = logging.getLogger(__name__)


def _normalize_dt_for_compare(dt: Optional[datetime]) -> Optional[datetime]:
    """Привести datetime к naive UTC для сравнений (asyncpg vs order.paid_at)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def is_promo_week_tariff_type(tariff_type: Optional[str]) -> bool:
    return (tariff_type or "").strip().lower().startswith("promo_test1week")


def is_base_tariff_type(tariff_type: Optional[str]) -> bool:
    return (tariff_type or "").strip().lower() == "base"


async def _resolve_pay_touch_key(
    storage: "UserStorage",
    *,
    user_id: int,
    order: Dict[str, Any],
    payment: Dict[str, Any],
) -> tuple[Optional[str], Optional[str]]:
    """Последнее смысловое касание до оплаты; fallback — orders.pay_last_touch_key."""
    paid_at = order.get("paid_at") or payment.get("completed_at")
    if paid_at and hasattr(storage, "get_last_marketing_touch_before"):
        touch = await storage.get_last_marketing_touch_before(
            user_id, paid_at, meaningful_only=True
        )
        if touch:
            key = touch.get("touch_key")
            ref_name = None
            if hasattr(storage, "resolve_touch_display_name"):
                ref_name = await storage.resolve_touch_display_name(
                    key, touch.get("ref_key")
                )
            elif hasattr(storage, "_touch_ref_name"):
                ref_name = await storage._touch_ref_name(
                    key, touch.get("ref_key")
                )
            return key, ref_name

    pay_touch_key = order.get("pay_last_touch_key")
    pay_ref_name = None
    if pay_touch_key and hasattr(storage, "get_ref_key_name"):
        pay_ref_name = await storage.get_ref_key_name(str(pay_touch_key))
    return pay_touch_key, pay_ref_name


@dataclass
class PriorPaymentRow:
    payment_id: int
    paid_at: datetime
    tariff_name: str
    tariff_type: str
    pay_touch_key: Optional[str]
    pay_ref_name: Optional[str]


@dataclass
class PaymentNoticeKind:
    code: str
    title: str
    extra_lines: List[str] = field(default_factory=list)


async def _fetch_prior_payments(
    storage: "UserStorage", user_id: int, exclude_payment_id: int
) -> List[PriorPaymentRow]:
    async with storage.get_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT
                p.id AS payment_id,
                COALESCE(p.completed_at, p.created_at) AS paid_at,
                t.name AS tariff_name,
                COALESCE(t.type, '') AS tariff_type,
                o.pay_last_touch_key,
                rk.name AS pay_ref_name
            FROM payments p
            JOIN orders o ON o.id = p.order_id
            JOIN tariffs t ON t.id = o.tariff_id
            LEFT JOIN ref_keys rk ON rk.ref_key = NULLIF(
                regexp_replace(o.pay_last_touch_key, '^ref_', ''), ''
            )
            WHERE p.user_id = $1
              AND p.status = 'succeeded'
              AND COALESCE(o.is_gift, FALSE) = FALSE
              AND p.id <> $2
            ORDER BY p.created_at ASC
            """,
            user_id,
            exclude_payment_id,
        )
    return [
        PriorPaymentRow(
            payment_id=int(r["payment_id"]),
            paid_at=r["paid_at"],
            tariff_name=r["tariff_name"] or "",
            tariff_type=r["tariff_type"] or "",
            pay_touch_key=r["pay_last_touch_key"],
            pay_ref_name=r["pay_ref_name"],
        )
        for r in rows
    ]


def _summarize_paid_licenses(rows: List[PriorPaymentRow]) -> str:
    if not rows:
        return "0"
    counts = Counter((r.tariff_name or "тариф").strip() for r in rows)
    parts = [
        f"{cnt}× «{html_mod.escape(name)}»" for name, cnt in sorted(counts.items())
    ]
    return f"{len(rows)} ({', '.join(parts)})"


def _summarize_base_tariffs(rows: List[PriorPaymentRow]) -> str:
    base_rows = [r for r in rows if is_base_tariff_type(r.tariff_type)]
    if not base_rows:
        return ""
    counts = Counter((r.tariff_name or "базовый").strip() for r in base_rows)
    parts = [f"{cnt}× «{html_mod.escape(name)}»" for name, cnt in counts.items()]
    return ", ".join(parts)


def _gap_days_since(expires_at: Optional[datetime], now: datetime) -> Optional[int]:
    if not expires_at:
        return None
    if expires_at > now:
        return 0
    return max(0, (now.date() - expires_at.date()).days)


def _renewal_extra_lines(
    *,
    prev_expires: Optional[datetime],
    prev_license_type: str,
    now: datetime,
) -> List[str]:
    extra: List[str] = []
    if prev_expires and prev_expires > now:
        days_left = max(0, (prev_expires.date() - now.date()).days)
        extra.append(
            f"📅 <b>До оплаты оставалось лицензии:</b> {russian_days_phrase(days_left)}"
        )
    if prev_license_type == "bonus_extension":
        extra.append(
            "🎁 <b>На момент оплаты:</b> действовал бонусный +1 день "
            "(<code>bonus_extension</code>)"
        )
    elif prev_license_type == "bonus":
        extra.append(
            "🎁 <b>На момент оплаты:</b> действовало бонусное продление "
            "(<code>bonus</code>)"
        )
    return extra


def _resume_extra_lines(
    *,
    prior: List[PriorPaymentRow],
) -> List[str]:
    extra: List[str] = []
    if prior:
        extra.append(
            f"📊 <b>Оплаченных лицензий ранее:</b> "
            f"{_summarize_paid_licenses(prior)}"
        )
    return extra


async def _club_absence_after_kick_line(
    storage: "UserStorage",
    *,
    user_id: int,
    payment_id: int,
    paid_at: Optional[datetime],
) -> Optional[str]:
    """Сколько дней участник не был в клубе после последнего исключения."""
    if not paid_at:
        return None

    anchor = _normalize_dt_for_compare(paid_at)
    kick_at: Optional[datetime] = None
    estimated = False

    if hasattr(storage, "get_last_club_exclusion_before"):
        kick_at = await storage.get_last_club_exclusion_before(user_id, anchor)

    if not kick_at and hasattr(storage, "get_last_subscription_expired_at"):
        kick_at = await storage.get_last_subscription_expired_at(
            user_id, before=anchor
        )
        if kick_at:
            estimated = True

    if not kick_at and hasattr(
        storage, "get_license_history_previous_expires_for_payment"
    ):
        prev_expires = await storage.get_license_history_previous_expires_for_payment(
            user_id, payment_id
        )
        prev_expires = _normalize_dt_for_compare(prev_expires)
        if prev_expires and prev_expires < anchor:
            grace = max(0, int(config.CLUB_GROUP_EXPIRED_LICENSE_GRACE_DAYS))
            kick_at = prev_expires + timedelta(days=grace)
            estimated = True

    kick_at = _normalize_dt_for_compare(kick_at)
    if not kick_at or kick_at >= anchor:
        return None

    days = max(0, (anchor.date() - kick_at.date()).days)
    if estimated:
        return (
            f"⏸ <b>Не был в клубе после исключения:</b> ~{russian_days_phrase(days)} "
            f"(оценка; точная запись об исключении отсутствует)"
        )
    return (
        f"⏸ <b>Не был в клубе после исключения:</b> {russian_days_phrase(days)}"
    )


def is_first_payment_notice(kind: PaymentNoticeKind) -> bool:
    """Первая оплата: тестовая неделя или первый базовый/основной платёж."""
    return kind.code in ("promo_week", "first_payment")


def days_in_bot_before_payment_phrase(days: int) -> str:
    n = max(0, int(days))
    if n == 0:
        return "0 дней"
    return russian_days_phrase(n)


def _days_between_dates(first_seen: datetime, paid_at: datetime) -> int:
    a = first_seen.date()
    b = paid_at.date()
    return max(0, (b - a).days)


async def _days_in_bot_line(
    storage: "UserStorage",
    *,
    user_id: int,
    paid_at: Optional[datetime],
) -> Optional[str]:
    if not paid_at or not hasattr(storage, "get_user_first_bot_seen_at"):
        return None
    first_seen = await storage.get_user_first_bot_seen_at(user_id)
    if not first_seen:
        return None
    days = _days_between_dates(first_seen, paid_at)
    return (
        f"📆 <b>Кол-во дней в боте:</b> "
        f"{html_mod.escape(days_in_bot_before_payment_phrase(days))}"
    )


def classify_subscription_payment(
    *,
    tariff_type: str,
    was_license_active: bool,
    license_before: Optional[Dict[str, Any]],
    prior: List[PriorPaymentRow],
    now: datetime,
    paid_at: Optional[datetime] = None,
) -> PaymentNoticeKind:
    prior_promo = [p for p in prior if is_promo_week_tariff_type(p.tariff_type)]
    prior_base = [p for p in prior if is_base_tariff_type(p.tariff_type)]
    prev_expires = (
        license_before.get("expires_at") if license_before else None
    )
    prev_license_type = (license_before or {}).get("license_type") or ""

    extra: List[str] = []

    if is_promo_week_tariff_type(tariff_type):
        if prior_promo:
            extra.append(
                f"📊 <b>Оплат promo_week ранее:</b> {len(prior_promo)}"
            )
        if prior_base:
            summary = _summarize_base_tariffs(prior)
            gap = _gap_days_since(prev_expires, now) if prev_expires else None
            extra.append(f"📊 <b>Базовые тарифы ранее:</b> {summary}")
            if gap is not None and gap > 0:
                extra.append(
                    f"⏸ <b>Перерыв до этой оплаты:</b> {russian_days_phrase(gap)}"
                )
        return PaymentNoticeKind(
            code="promo_week",
            title="💰 НОВЫЙ ПЛАТЕЖ — ТЕСТОВАЯ НЕДЕЛЯ",
            extra_lines=extra,
        )

    last_prior = prior[-1] if prior else None
    if (
        is_base_tariff_type(tariff_type)
        and was_license_active
        and last_prior
        and is_promo_week_tariff_type(last_prior.tariff_type)
        and prev_expires
        and prev_expires > now
    ):
        return PaymentNoticeKind(
            code="promo_to_base",
            title="💰 НОВАЯ ОПЛАТА — ПЕРЕХОД С ТЕСТОВОЙ НЕДЕЛИ НА БАЗОВЫЙ ТАРИФ",
            extra_lines=[
                f"📎 <b>Последняя оплата:</b> {html_mod.escape(last_prior.tariff_name)} "
                f"(promo_week, до {prev_expires.strftime('%d.%m.%Y')})",
            ],
        )

    if (
        was_license_active
        and is_base_tariff_type(tariff_type)
        and prior_base
    ):
        return PaymentNoticeKind(
            code="renewal",
            title="💰 ПРОДЛЕНИЕ ЛИЦЕНЗИИ",
            extra_lines=_renewal_extra_lines(
                prev_expires=prev_expires,
                prev_license_type=prev_license_type,
                now=now,
            ),
        )

    non_promo_before = [
        p for p in prior if not is_promo_week_tariff_type(p.tariff_type)
    ]

    # Была base-история, лицензия уже неактивна — возобновление (в т.ч. если ещё в кэше группы).
    if not was_license_active and prior_base:
        return PaymentNoticeKind(
            code="resume",
            title="💰 ВОЗОБНОВЛЕНИЕ ЛИЦЕНЗИИ",
            extra_lines=_resume_extra_lines(prior=prior),
        )

    # Первая «основная» оплата (в истории только promo_week или пусто).
    if not non_promo_before:
        return PaymentNoticeKind(
            code="first_payment",
            title="💰 НОВЫЙ ПЛАТЕЖ",
        )

    # Активная лицензия, в истории есть не-promo оплаты, но не base — всё равно продление.
    if was_license_active:
        summary = _summarize_base_tariffs(non_promo_before) or ", ".join(
            html_mod.escape((p.tariff_name or p.tariff_type or "тариф").strip())
            for p in non_promo_before
        )
        extra = _renewal_extra_lines(
            prev_expires=prev_expires,
            prev_license_type=prev_license_type,
            now=now,
        )
        extra.insert(
            0,
            f"📊 <b>Оплаты ранее (не promo_week):</b> {summary}",
        )
        return PaymentNoticeKind(
            code="renewal",
            title="💰 ПРОДЛЕНИЕ ЛИЦЕНЗИИ",
            extra_lines=extra,
        )

    # Неактивная лицензия, но были не-promo оплаты (без base в истории — редкий тариф).
    return PaymentNoticeKind(
        code="resume",
        title="💰 ВОЗОБНОВЛЕНИЕ ЛИЦЕНЗИИ",
        extra_lines=_resume_extra_lines(prior=prior),
    )


async def build_subscription_payment_admin_html(
    storage: "UserStorage",
    *,
    order: Dict[str, Any],
    payment: Dict[str, Any],
    rub_amount: float,
    license_before: Optional[Dict[str, Any]],
    license_after: Optional[Dict[str, Any]],
    was_license_active: bool,
) -> str:
    user_id = int(order["user_id"])
    payment_id = int(payment["id"])
    tariff_type = (order.get("tariff_type") or "").strip()
    now = datetime.now()

    prior = await _fetch_prior_payments(storage, user_id, payment_id)

    paid_at = order.get("paid_at") or payment.get("completed_at")
    if isinstance(paid_at, str):
        try:
            paid_at = datetime.fromisoformat(paid_at.replace("Z", "+00:00"))
        except ValueError:
            paid_at = None

    kind = classify_subscription_payment(
        tariff_type=tariff_type,
        was_license_active=was_license_active,
        license_before=license_before,
        prior=prior,
        now=now,
        paid_at=paid_at,
    )

    user_data = payment.get("user_telegram_data", {})
    if isinstance(user_data, str):
        user_data = json.loads(user_data)
    full_name = f"{user_data.get('first_name', '')} {user_data.get('last_name', '')}".strip()
    full_name = full_name or "Не указано"
    username_display = (
        "@" + user_data["username"] if user_data.get("username") else "нет username"
    )

    arrival_source = "неизвестно"
    if hasattr(storage, "get_first_start_source_display"):
        arrival_source = await storage.get_first_start_source_display(user_id) or "неизвестно"
    elif hasattr(storage, "get_last_referral_source"):
        arrival_source = await storage.get_last_referral_source(user_id) or "неизвестно"

    pay_touch_key, pay_ref_name = await _resolve_pay_touch_key(
        storage,
        user_id=user_id,
        order=order,
        payment=payment,
    )
    pay_source = format_touch_key_html(pay_touch_key, pay_ref_name)

    expires_str = (
        license_after["expires_at"].strftime("%d.%m.%Y")
        if license_after and license_after.get("expires_at")
        else "N/A"
    )
    assistant_msgs_count = await storage.get_assistant_messages_count(user_id)

    lines = [
        kind.title,
        "",
        f"📋 <b>Тариф:</b> {html_mod.escape(str(order.get('tariff_name') or ''))}",
        f"💰 <b>Сумма:</b> {order['amount']} {order['currency']}",
        f"💳 <b>В рублях:</b> {rub_amount:.2f} RUB",
        f"👤 <b>Пользователь:</b> {html_mod.escape(full_name)}",
        f"🆔 <b>User ID:</b> <code>{user_id}</code>",
        f"📱 <b>Username:</b> {html_mod.escape(username_display)}",
    ]
    if is_first_payment_notice(kind) and not prior:
        days_line = await _days_in_bot_line(
            storage, user_id=user_id, paid_at=paid_at
        )
        if days_line:
            lines.append(days_line)
    lines.extend(
        [
            f"🔗 <b>Источник клиента:</b> {html_mod.escape(arrival_source)}",
            f"💳 <b>Триггер к оплате:</b> {pay_source}",
            f"💬 <b>Вопросов Боту:</b> {html_mod.escape(str(assistant_msgs_count or '0'))}",
            f"📅 <b>Лицензия до:</b> {expires_str}",
        ]
    )
    if kind.code == "resume":
        absence_line = await _club_absence_after_kick_line(
            storage,
            user_id=user_id,
            payment_id=payment_id,
            paid_at=paid_at,
        )
        if absence_line:
            lines.append(absence_line)
    lines.extend(kind.extra_lines)
    return "\n".join(lines)
