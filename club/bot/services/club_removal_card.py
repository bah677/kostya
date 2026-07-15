"""Карточка админ-уведомления при исключении пользователя из группы клуба."""

from __future__ import annotations

import html as html_mod
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, List, Optional, TYPE_CHECKING
from zoneinfo import ZoneInfo

from config import config

if TYPE_CHECKING:
    from storage.user_storage import UserStorage

logger = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")

REASON_BONUS_EXPIRED = "bonus_expired"
REASON_NIGHTLY_AUDIT = "nightly_audit"

_REASON_TITLES = {
    REASON_BONUS_EXPIRED: (
        "Истёк бонусный +1 день после окончания платной подписки",
        "Кик по расписанию <code>subscription_reminder</code> "
        "(<code>bonus_extension</code> закончился вчера, МСК).",
    ),
    REASON_NIGHTLY_AUDIT: (
        "Нет действующей подписки в группе",
        "Ночной аудит <code>club_group</code>: в кэше участник, "
        "в <code>license</code> нет active с будущим <code>expires_at</code> "
        "(с учётом <code>CLUB_GROUP_EXPIRED_LICENSE_GRACE_DAYS</code>).",
    ),
}


@dataclass
class PaymentLine:
    paid_at: datetime
    amount_rub: float
    tariff_name: str
    tariff_type: str
    pay_source: str


@dataclass
class RemovalCardData:
    user_id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    arrival_source: str = "—"
    first_touch_label: str = "—"
    payments: List[PaymentLine] = field(default_factory=list)
    payments_total_rub: float = 0.0
    last_paid_tariff: str = "—"
    last_paid_at: Optional[datetime] = None
    last_paid_source: str = "—"
    first_paid_date: Optional[date] = None
    last_paid_expires_date: Optional[date] = None
    days_in_club: Optional[int] = None
    bonus_line: Optional[str] = None
    group_messages: int = 0
    license_type: Optional[str] = None
    license_status: Optional[str] = None
    license_expires: Optional[datetime] = None


def _esc(s: Any) -> str:
    return html_mod.escape(str(s) if s is not None else "—")


def _fmt_date(d: Optional[date]) -> str:
    if not d:
        return "—"
    return d.strftime("%d.%m.%Y")


def _fmt_dt_msk(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    if dt.tzinfo is None:
        return dt.strftime("%d.%m.%Y %H:%M")
    return dt.astimezone(MSK).strftime("%d.%m.%Y %H:%M")


def _money(amount: float) -> str:
    return f"{int(round(amount)):,}".replace(",", " ") + " ₽"


def _touch_label(key: Optional[str], kind: Optional[str], ref_name: Optional[str]) -> str:
    if not key and not kind:
        return "—"
    if ref_name and key:
        return f"{ref_name} (<code>{_esc(key)}</code>)"
    if key:
        return f"<code>{_esc(key)}</code>"
    return _esc(kind or "—")


def _tariff_display(name: str, tariff_type: str) -> str:
    n = (name or "").strip()
    if n:
        return n
    t = (tariff_type or "").strip()
    if t.startswith("promo_test"):
        return "Пробная неделя"
    return t or "—"


def _is_paid_subscription_tariff(tariff_type: str) -> bool:
    t = (tariff_type or "").lower()
    if not t or t == "bonus":
        return False
    if "bonus" in t:
        return False
    return True


async def _resolve_touch_name(storage: UserStorage, touch_key: Optional[str]) -> Optional[str]:
    if not touch_key:
        return None
    if hasattr(storage, "get_ref_key_name"):
        return await storage.get_ref_key_name(touch_key)
    return None


async def collect_removal_card_data(
    storage: UserStorage, user_id: int
) -> RemovalCardData:
    data = RemovalCardData(user_id=user_id)
    club_gid = int(config.CLUB_GROUP_ID or 0)

    try:
        async with storage.get_connection() as conn:
            user = await conn.fetchrow(
                """
                SELECT user_id, first_name, last_name, username,
                       first_touch_key, first_touch_kind
                FROM users WHERE user_id = $1
                """,
                user_id,
            )
            if user:
                data.first_name = user["first_name"]
                data.last_name = user["last_name"]
                data.username = user["username"]
                ref_name = await _resolve_touch_name(storage, user["first_touch_key"])
                data.first_touch_label = _touch_label(
                    user["first_touch_key"], user["first_touch_kind"], ref_name
                )

            if hasattr(storage, "get_first_start_source_display"):
                data.arrival_source = (
                    await storage.get_first_start_source_display(user_id) or "—"
                )
            elif hasattr(storage, "get_first_start_source"):
                data.arrival_source = await storage.get_first_start_source(user_id) or "—"

            pay_rows = await conn.fetch(
                """
                SELECT
                    p.created_at AS paid_at,
                    COALESCE(p.amount_rub, o.amount_rub, o.amount, 0)::float AS amount_rub,
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
                ORDER BY p.created_at ASC
                """,
                user_id,
            )

            for r in pay_rows:
                src = _touch_label(
                    r["pay_last_touch_key"], None, r["pay_ref_name"]
                )
                line = PaymentLine(
                    paid_at=r["paid_at"],
                    amount_rub=float(r["amount_rub"] or 0),
                    tariff_name=_tariff_display(r["tariff_name"], r["tariff_type"]),
                    tariff_type=r["tariff_type"] or "",
                    pay_source=src,
                )
                data.payments.append(line)
                data.payments_total_rub += line.amount_rub

            paid_subs = [
                p for p in data.payments if _is_paid_subscription_tariff(p.tariff_type)
            ]
            if paid_subs:
                last = paid_subs[-1]
                data.last_paid_tariff = last.tariff_name
                data.last_paid_at = last.paid_at
                data.last_paid_source = last.pay_source
                data.first_paid_date = paid_subs[0].paid_at.date()

            lic = await conn.fetchrow(
                """
                SELECT license_type, status, expires_at
                FROM license
                WHERE user_id = $1
                ORDER BY updated_at DESC NULLS LAST, id DESC
                LIMIT 1
                """,
                user_id,
            )
            if lic:
                data.license_type = lic["license_type"]
                data.license_status = lic["status"]
                data.license_expires = lic["expires_at"]

            hist = await conn.fetchrow(
                """
                SELECT
                    MAX(new_expires_at) FILTER (
                        WHERE source IS DISTINCT FROM 'bonus_extension_offer'
                    ) AS last_paid_expires,
                    MAX(created_at) FILTER (
                        WHERE source = 'bonus_extension_offer'
                    ) AS bonus_at
                FROM license_history
                WHERE user_id = $1
                """,
                user_id,
            )
            if hist and hist["last_paid_expires"]:
                exp = hist["last_paid_expires"]
                if hasattr(exp, "date"):
                    data.last_paid_expires_date = (
                        exp.astimezone(MSK).date()
                        if getattr(exp, "tzinfo", None)
                        else exp.date()
                    )
                else:
                    data.last_paid_expires_date = exp

            if not data.last_paid_expires_date and data.license_expires:
                if (data.license_type or "") != "bonus_extension":
                    exp = data.license_expires
                    data.last_paid_expires_date = (
                        exp.astimezone(MSK).date()
                        if getattr(exp, "tzinfo", None)
                        else exp.date()
                    )

            bonus_row = await conn.fetchrow(
                """
                SELECT previous_expires_at, new_expires_at, meta
                FROM license_history
                WHERE user_id = $1 AND source = 'bonus_extension_offer'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                user_id,
            )
            if bonus_row:
                p0 = bonus_row["previous_expires_at"]
                p1 = bonus_row["new_expires_at"]
                data.bonus_line = (
                    f"Бонус +1 день: {_fmt_dt_msk(p0)} → {_fmt_dt_msk(p1)}"
                )
            elif (data.license_type or "") == "bonus_extension" and data.license_expires:
                data.bonus_line = (
                    f"Бонусный период до {_fmt_dt_msk(data.license_expires)}"
                )

            if data.first_paid_date and data.last_paid_expires_date:
                data.days_in_club = max(
                    0, (data.last_paid_expires_date - data.first_paid_date).days
                )

            if club_gid:
                data.group_messages = int(
                    await conn.fetchval(
                        """
                        SELECT COUNT(*)::int
                        FROM messages
                        WHERE user_id = $1
                          AND chat_id = $2
                          AND role = 'user'
                          AND deleted_at IS NULL
                          AND COALESCE(message_type, '') <> 'callback'
                        """,
                        user_id,
                        club_gid,
                    )
                    or 0
                )

    except Exception as e:
        logger.exception("collect_removal_card_data user=%s: %s", user_id, e)

    return data


def format_club_removal_card_html(
    data: RemovalCardData,
    *,
    reason: str,
    reason_extra: Optional[str] = None,
) -> str:
    title, detail = _REASON_TITLES.get(
        reason,
        ("Исключён из закрытой группы", _esc(reason)),
    )

    fn = (data.first_name or "").strip()
    ln = (data.last_name or "").strip()
    full_name = (fn + (" " + ln if ln else "")).strip() or "—"
    un = f"@{_esc(data.username)}" if data.username else "—"

    pay_lines: List[str] = []
    if data.payments:
        for p in data.payments[-8:]:
            d = _fmt_dt_msk(p.paid_at).split()[0]
            pay_lines.append(
                f"  · {d} — {_esc(p.tariff_name)} {_money(p.amount_rub)}"
            )
        if len(data.payments) > 8:
            pay_lines.insert(0, f"  <i>… ещё {len(data.payments) - 8} оплат</i>")
    else:
        pay_lines.append("  · нет успешных оплат")

    extra = f"\n<i>{_esc(reason_extra)}</i>" if reason_extra else ""
    grace = ""
    if reason == REASON_NIGHTLY_AUDIT:
        g = int(getattr(config, "CLUB_GROUP_EXPIRED_LICENSE_GRACE_DAYS", 0) or 0)
        if g:
            grace = f"\n<i>Отсрочка после expires_at: {g} дн.</i>"

    blocks = [
        "<b>🚪 Исключён из закрытой группы</b>",
        f"<b>Причина:</b> {_esc(title)}",
        f"<i>{detail}</i>{extra}{grace}",
        "",
        "<b>👤 Пользователь</b>",
        f"  Имя: {_esc(full_name)}",
        f"  Ник: {un}",
        f"  ID: <code>{data.user_id}</code>",
        "",
        "<b>📥 Приход в бота</b>",
        f"  Первый /start: {_esc(data.arrival_source)}",
        f"  First touch: {data.first_touch_label}",
        "",
        f"<b>💳 Оплаты</b> (успешных: {len(data.payments)}, "
        f"{_money(data.payments_total_rub)})",
        *pay_lines,
        "",
        "<b>📌 Последняя платная подписка</b>",
        f"  Тариф: {_esc(data.last_paid_tariff)}",
        f"  Оплата: {_fmt_dt_msk(data.last_paid_at)}",
        f"  Источник оплаты: {data.last_paid_source}",
        "",
        "<b>⏱ В клубе</b>",
        f"  Первая оплата: {_fmt_date(data.first_paid_date)}",
        f"  Окончание платного доступа: {_fmt_date(data.last_paid_expires_date)}",
    ]

    if data.days_in_club is not None:
        blocks.append(f"  Всего дней (оценка): <b>{data.days_in_club}</b>")
    if data.bonus_line:
        blocks.append(f"  {_esc(data.bonus_line)}")

    blocks.extend(
        [
            "",
            "<b>💬 Группа клуба</b>",
            f"  Сообщений в чате: <b>{data.group_messages}</b>",
            "",
            "<b>📋 Сейчас в БД</b>",
            f"  license: {_esc(data.license_type)} · {_esc(data.license_status)}",
            f"  expires_at: {_fmt_dt_msk(data.license_expires)}",
        ]
    )

    return "\n".join(blocks)


async def build_club_removal_card_html(
    storage: UserStorage,
    user_id: int,
    *,
    reason: str,
    reason_extra: Optional[str] = None,
) -> str:
    data = await collect_removal_card_data(storage, user_id)
    return format_club_removal_card_html(
        data, reason=reason, reason_extra=reason_extra
    )
