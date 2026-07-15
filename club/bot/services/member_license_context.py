"""Факты о подписке участника для member-агента (без выдумок)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from storage.license_types import (
    LICENSE_TYPE_ADMIN_GRANT,
    LICENSE_TYPE_ADMIN_SUBSCRIPTION,
    LICENSE_TYPE_BONUS,
    LICENSE_TYPE_BONUS_EXTENSION,
    LICENSE_TYPE_SUBSCRIPTION,
)

MSK = ZoneInfo("Europe/Moscow")

_ACCESS_HINT = re.compile(
    r"доступ|подписк|лиценз|в\s+клуб|зайти|войти|пуска|выкинул|"
    r"осталось|конча|истека|продл|/subs|/club|/payment|"
    r"активн|работает\s+ли|есть\s+ли|что\s+у\s+меня\s+с|"
    r"как\s+там\s+с|мой\s+статус|мне\s+можно|меня\s+пуст|"
    r"в\s+групп|в\s+чат|участник\s+ли|оплач|продлен",
    re.IGNORECASE,
)

_SHORT_ACK = re.compile(
    r"^(?:да|ок|окей|ага|угу|конечно|давай|расскажи|хочу|please|yes|"
    r"конесно|интересно|продолжай|слушаю|дальше|ладно|хорошо|ну)[\s!.?…]*$",
    re.IGNORECASE,
)

_ACK_PART = re.compile(
    r"^(?:да|ок|окей|ага|угу|конечно|конесно|давай|расскажи|хочу|"
    r"интересно|продолжай|слушаю|дальше|ладно|хорошо|ну)(?:[\s!.?…]|$)",
    re.IGNORECASE,
)

_VAGUE_ACCESS_REPLY = re.compile(
    r"вс[её]\s+(?:хорошо|в\s+порядке|ок|норм)|"
    r"доступ\s+(?:есть|открыт|активен)|"
    r"ничего\s+не\s+беспокой|можешь\s+не\s+переживать",
    re.IGNORECASE,
)

_GREETING_START = re.compile(
    r"^\s*(?:<[^>]+>\s*)*(?:привет|здравствуй|добрый\s+(?:день|вечер|утро))",
    re.IGNORECASE,
)

_CLUB_LINK_PROBLEM = re.compile(
    r"не\s+работает|не\s+получил|не\s+пришл|не\s+активн|"
    r"истек|expired|устарел|"
    r"не\s+пуска|не\s+заход|не\s+могу\s+(?:зайти|войти|попасть)|"
    r"вс[её]\s+равно\s+не",
    re.IGNORECASE,
)

_CLUB_LINK_CONTEXT = re.compile(
    r"ссылк|invite|вступ|клуб|/club|групп|чат",
    re.IGNORECASE,
)


def _fmt_dt_msk(dt: Any) -> str:
    if dt is None:
        return "—"
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=MSK)
        return dt.astimezone(MSK).strftime("%d.%m.%Y")
    return str(dt)[:10]


def _license_type_label(license_type: str) -> str:
    labels = {
        LICENSE_TYPE_SUBSCRIPTION: "оплаченная подписка",
        LICENSE_TYPE_ADMIN_GRANT: "подарок от администрации",
        LICENSE_TYPE_ADMIN_SUBSCRIPTION: "админская подписка (команда проекта)",
        LICENSE_TYPE_BONUS: "бонусное продление",
        LICENSE_TYPE_BONUS_EXTENSION: "бонусное продление",
    }
    return labels.get(license_type or "", license_type or "участие в клубе")


def looks_like_access_question(text: str) -> bool:
    return bool(_ACCESS_HINT.search((text or "").strip()))


def looks_like_club_link_problem(text: str) -> bool:
    """Жалоба на нерабочую/просроченную ссылку в клуб."""
    t = (text or "").strip()
    if not t:
        return False
    if not _CLUB_LINK_PROBLEM.search(t):
        return False
    return bool(_CLUB_LINK_CONTEXT.search(t)) or looks_like_access_question(t)


def looks_like_short_ack(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if _SHORT_ACK.match(t):
        return True
    parts = [p.strip() for p in re.split(r"[,;]", t) if p.strip()]
    if 2 <= len(parts) <= 4 and all(_ACK_PART.match(p) for p in parts):
        return True
    return False


def access_reply_is_too_vague(draft: str, *, has_active_license: bool) -> bool:
    """Ответ про доступ без конкретики (дата, тип, команды)."""
    body = (draft or "").strip()
    if not body:
        return True
    if not has_active_license:
        return False
    low = body.lower()
    if _VAGUE_ACCESS_REPLY.search(low):
        return True
    if _GREETING_START.search(body) and len(body) < 280:
        if not any(
            kw in low
            for kw in (
                "до ",
                "действ",
                "подписк",
                "участ",
                "админ",
                "/subs",
                "/club",
                "2100",
                "без огранич",
            )
        ):
            return True
    return False


def build_member_license_facts_addon(license_row: Optional[Dict[str, Any]]) -> str:
    """Блок «ФАКТЫ О ДОСТУПЕ» для system prompt."""
    if not license_row:
        return (
            "=== ФАКТЫ О ДОСТУПЕ (из БД) ===\n"
            "Активной лицензии нет.\n"
            "Если спрашивают про доступ — скажи честно и предложи /payment или /support."
        )

    expires = license_row.get("expires_at")
    expires_str = _fmt_dt_msk(expires)
    ltype = str(license_row.get("license_type") or LICENSE_TYPE_SUBSCRIPTION)
    label = _license_type_label(ltype)

    long_admin = False
    if isinstance(expires, datetime) and expires.year >= 2099:
        long_admin = ltype == LICENSE_TYPE_ADMIN_SUBSCRIPTION

    lines = [
        "=== ФАКТЫ О ДОСТУПЕ (из БД — отвечай ими, не выдумывай) ===",
        "Статус: ✅ активное участие в клубе",
        f"Тип: {label}",
    ]
    if long_admin:
        lines.append("Срок: без ограничения по дате (админская подписка)")
    else:
        lines.append(f"Действует до: {expires_str} (МСК)")

    lines.extend(
        [
            "Войти в группу клуба: команда /club",
            "Подробности и дата: команда /subs",
            "Продление (если спросят): /payment",
            "",
            "Если человек спрашивает «что с доступом», «пускает ли», «есть ли подписка» — "
            "ответь прямо этими фактами. Не ограничивайся «всё в порядке» без даты и типа.",
        ]
    )
    return "\n".join(lines)


def build_access_status_reply_html(license_row: Optional[Dict[str, Any]]) -> str:
    """Готовый ответ на вопрос про доступ — факты из БД, без выдумок."""
    if not license_row:
        return (
            "Сейчас в базе <b>нет активной подписки</b> на клуб.\n\n"
            "Оформить участие: /payment\n"
            "Если кажется, что это ошибка — напишите в /support."
        )

    expires = license_row.get("expires_at")
    ltype = str(license_row.get("license_type") or LICENSE_TYPE_SUBSCRIPTION)
    label = _license_type_label(ltype)

    long_admin = (
        isinstance(expires, datetime)
        and expires.year >= 2099
        and ltype == LICENSE_TYPE_ADMIN_SUBSCRIPTION
    )

    lines = ["<b>У вас активное участие в клубе.</b>"]
    if long_admin:
        lines.append(f"Тип: {label} — без ограничения по дате.")
    else:
        lines.append(f"Тип: {label}.")
        lines.append(f"Действует до <b>{_fmt_dt_msk(expires)}</b> (МСК).")

    lines.extend(
        [
            "",
            "Войти в группу: /club",
            "Подробности и дата: /subs",
        ]
    )
    if not long_admin:
        lines.append("Продлить, когда придёт срок: /payment")
    return "\n".join(lines)


def build_natural_language_turn_addon(user_message: str) -> str:
    """Подсказки на текущую реплику (короткий ответ, прямой вопрос)."""
    parts: list[str] = []
    um = (user_message or "").strip()

    if looks_like_access_question(um):
        parts.append(
            "🔴 Вопрос про доступ/подписку — ответь фактами из блока «ФАКТЫ О ДОСТУПЕ»: "
            "статус, тип, дата (или «без ограничения»). "
            "Запрещено ограничиваться «всё в порядке» / «доступ есть» без деталей."
        )
    elif um.endswith("?"):
        parts.append(
            "🔴 Сейчас прямой вопрос — не начинай с «Привет» / «Здравствуйте»; "
            "ответь по сути с первого предложения."
        )
    if looks_like_short_ack(um):
        parts.append(
            "🔴 Короткое согласие («да», «расскажи», «конечно») — это продолжение "
            "предыдущей темы. Посмотри свой последний ответ в истории и развивай его, "
            "не задавай заново «чем помочь?»."
        )
    if not parts:
        return ""
    return "=== ПОДСКАЗКА НА ЭТУ РЕПЛИКУ ===\n" + "\n".join(parts)
