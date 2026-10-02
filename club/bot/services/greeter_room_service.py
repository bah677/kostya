"""Закрытый чат команды встречающих: инвайт в личку; кик пока заменён на алерт в ТП."""

from __future__ import annotations

import html as html_mod
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, List, Optional, Set

from aiogram.enums import ParseMode

from bot.texts import ru_gift_wave as txt
from bot.utils.admin_channel import send_admin_html_message
from bot.utils.telegram_send import call_with_flood_retry
from config import config

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)


def greeter_chat_configured() -> bool:
    return bool(
        getattr(config, "GREETER_CHAT_ENABLED", True)
        and int(getattr(config, "GREETER_CHAT_ID", 0) or 0) != 0
    )


def greeter_chat_id() -> int:
    return int(getattr(config, "GREETER_CHAT_ID", 0) or 0)


def greeter_topic_link() -> str:
    """Публичная ссылка на основной топик: t.me/c/<internal>/<topic>."""
    chat_id = greeter_chat_id()
    topic = int(getattr(config, "GREETER_CHAT_TOPIC_ID", 0) or 0)
    raw = str(chat_id)
    if raw.startswith("-100"):
        internal = raw[4:]
    else:
        internal = raw.lstrip("-")
    if topic > 0:
        return f"https://t.me/c/{internal}/{topic}"
    return f"https://t.me/c/{internal}"


async def _is_project_admin(user_storage, user_id: int) -> bool:
    if config.SUPER_ADMIN_ID and int(user_id) == int(config.SUPER_ADMIN_ID):
        return True
    try:
        return bool(await user_storage.is_telegram_admin_id(int(user_id)))
    except Exception:
        return False


def _user_label(user: Optional[dict], user_id: int) -> str:
    if not user:
        return f"<code>{user_id}</code>"
    name = " ".join(
        p for p in (user.get("first_name") or "", user.get("last_name") or "") if p
    ).strip()
    un = (user.get("username") or "").strip()
    parts = []
    if name:
        parts.append(html_mod.escape(name))
    if un:
        parts.append(f"@{html_mod.escape(un)}")
    parts.append(f"<code>{user_id}</code>")
    return " · ".join(parts)


async def create_greeter_chat_invite_link(bot: "Bot") -> Optional[str]:
    if not greeter_chat_configured():
        return None
    chat_id = greeter_chat_id()
    ttl = int(getattr(config, "GREETER_CHAT_INVITE_TTL_HOURS", 48) or 48)
    expire = datetime.now() + timedelta(hours=max(1, ttl))

    async def _create():
        return await bot.create_chat_invite_link(
            chat_id=chat_id,
            member_limit=1,
            expire_date=expire,
            name="greeter-room",
        )

    try:
        link_obj = await call_with_flood_retry(
            _create, max_attempts=4, log_prefix="greeter_chat_invite"
        )
        return link_obj.invite_link
    except Exception as e:
        logger.error("greeter chat invite create: %s", e, exc_info=True)
        return None


async def send_greeter_chat_invite_dm(
    bot: "Bot", user_id: int, *, for_admin: bool = False
) -> bool:
    """Личка: персональная ссылка в чат встречающих."""
    if not greeter_chat_configured():
        return False
    link = await create_greeter_chat_invite_link(bot)
    if not link:
        return False
    topic = greeter_topic_link()
    body = (
        txt.GREETER_ROOM_ADMIN_INVITE_HTML
        if for_admin
        else txt.GREETER_ROOM_INVITE_HTML
    ).format(
        link=html_mod.escape(link),
        topic_link=html_mod.escape(topic),
    )
    try:
        await bot.send_message(
            user_id,
            body,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return True
    except Exception as e:
        logger.error("greeter chat DM uid=%s: %s", user_id, e)
        return False


async def unban_greeter_chat_member(bot: "Bot", user_id: int) -> bool:
    """Снять ограничение входа в чат встречающих (после прежнего ban+unban кика)."""
    if not greeter_chat_configured():
        return False
    chat_id = greeter_chat_id()
    try:
        await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
        logger.info("greeter chat: unban uid=%s", user_id)
        return True
    except Exception as e:
        logger.warning("greeter chat unban uid=%s: %s", user_id, e)
        return False


async def notify_greeter_chat_removal_needed(
    bot: "Bot",
    user_storage,
    user_id: int,
    *,
    reason: str,
) -> bool:
    """Вместо автокика: алерт в топик тикетов ТП клуба (SUPPORT_THREAD_ID)."""
    if not greeter_chat_configured():
        return False
    if await _is_project_admin(user_storage, user_id):
        logger.info("greeter chat: skip removal notice for admin uid=%s", user_id)
        return False

    user = None
    try:
        user = await user_storage.get_user(int(user_id))
    except Exception:
        pass
    who = _user_label(user, int(user_id))
    reason_s = html_mod.escape((reason or "без указания причины").strip())
    chat_id = greeter_chat_id()
    text = (
        "⚠️ <b>Чат встречающих — нужно удалить вручную</b>\n\n"
        f"👤 {who}\n"
        f"📋 Причина: {reason_s}\n"
        f"💬 Чат: <code>{chat_id}</code>\n\n"
        "Автоудаление временно выключено. Пожалуйста, уберите человека "
        "из чата команды встречающих вручную."
    )
    tid = int(getattr(config, "SUPPORT_THREAD_ID", 0) or 0)
    ok = await send_admin_html_message(
        bot,
        text,
        thread_id=tid if tid > 0 else None,
    )
    if ok:
        logger.info(
            "greeter chat: removal notice uid=%s reason=%s", user_id, reason
        )
    else:
        logger.warning(
            "greeter chat: removal notice FAILED uid=%s reason=%s", user_id, reason
        )
    return bool(ok)


async def remove_from_greeter_chat(
    bot: "Bot",
    user_storage,
    user_id: int,
    *,
    reason: str = "снят с пула встречающих",
) -> bool:
    """Раньше: ban+unban. Сейчас: только уведомление в топик ТП."""
    return await notify_greeter_chat_removal_needed(
        bot, user_storage, user_id, reason=reason
    )


async def on_greeter_activated(bot: "Bot", user_storage, user_id: int) -> bool:
    """После добавления в пул — инвайт в чат команды."""
    return await send_greeter_chat_invite_dm(bot, user_id, for_admin=False)


async def on_greeter_deactivated(
    bot: "Bot",
    user_storage,
    user_id: int,
    *,
    reason: str = "снят с пула встречающих",
) -> bool:
    """После снятия с пула — алерт в ТП вместо кика."""
    return await remove_from_greeter_chat(
        bot, user_storage, user_id, reason=reason
    )


async def sync_greeter_chat_invites(
    bot: "Bot",
    user_storage,
    *,
    include_admins: bool = True,
    pause_sec: float = 0.4,
) -> dict:
    """Разослать инвайты всем активным встречающим (+ админам)."""
    import asyncio

    if not greeter_chat_configured():
        return {"ok": 0, "fail": 0, "total": 0, "skipped": True}

    greeter_ids: Set[int] = {
        int(uid)
        for uid in await user_storage.list_active_club_greeter_ids()
        if uid > 0
    }
    admin_ids: Set[int] = set()
    if include_admins:
        for row in await user_storage.list_telegram_admin_ids():
            aid = int(row["telegram_user_id"])
            if aid > 0:
                admin_ids.add(aid)
        if config.SUPER_ADMIN_ID:
            admin_ids.add(int(config.SUPER_ADMIN_ID))

    targets: List[tuple[int, bool]] = []
    seen: Set[int] = set()
    for uid in sorted(greeter_ids):
        targets.append((uid, False))
        seen.add(uid)
    for uid in sorted(admin_ids):
        if uid in seen:
            continue
        targets.append((uid, True))
        seen.add(uid)

    ok = fail = 0
    for uid, as_admin in targets:
        if await send_greeter_chat_invite_dm(bot, uid, for_admin=as_admin):
            ok += 1
        else:
            fail += 1
        await asyncio.sleep(pause_sec)
    return {"ok": ok, "fail": fail, "total": len(targets)}
