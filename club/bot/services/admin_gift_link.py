"""Админские gift-ссылки: /giftlink → ?start=agift_CODE."""

from __future__ import annotations

import html as html_mod
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram.enums import ParseMode
from aiogram.types import Message, User

from bot.utils.admin_channel import send_admin_html_message
from bot.utils.telegram_identity import resolve_telegram_bot_username
from config import config, russian_days_phrase

logger = logging.getLogger(__name__)

_CLUB_NAME = "Любящие Бога"
START_PREFIX = "agift_"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware_utc(dt: datetime) -> datetime:
    """Postgres timestamptz приходит aware; naive считаем UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_admin_label(admin: User) -> str:
    if admin.username:
        return f"@{html_mod.escape(admin.username)} (<code>{admin.id}</code>)"
    name = " ".join(
        p for p in (admin.first_name or "", admin.last_name or "") if p
    ).strip()
    if name:
        return f"{html_mod.escape(name)} (<code>{admin.id}</code>)"
    return f"<code>{admin.id}</code>"


async def create_admin_gift_link(
    *,
    user_storage,
    bot,
    admin_user: User,
    days: int,
    note: Optional[str] = None,
) -> str:
    """Создаёт одноразовую ссылку. Возвращает HTML-ответ админу."""
    if days < 1 or days > 3650:
        return "❌ Число дней должно быть от 1 до 3650."

    link_days = int(getattr(config, "GIFT_LINK_VALIDITY_DAYS", 30) or 30)
    link_days = max(1, min(3650, link_days))
    gift_code = secrets.token_hex(8).upper()
    expires_at = _utcnow() + timedelta(days=link_days)

    row = await user_storage.create_admin_gift_code(
        gift_code=gift_code,
        days=days,
        created_by=admin_user.id,
        expires_at=expires_at,
        note=note,
    )
    if not row:
        return "❌ Не удалось создать ссылку (ошибка БД)."

    bot_username = await resolve_telegram_bot_username(bot)
    if not bot_username:
        bot_username = (config.TELEGRAM_BOT_USERNAME or "Talk_God_Bot").lstrip("@")
    link = f"https://t.me/{bot_username}?start={START_PREFIX}{gift_code}"

    days_phrase = russian_days_phrase(days)
    link_phrase = russian_days_phrase(link_days)
    note_line = ""
    if note and note.strip():
        note_line = f"\n📝 Заметка: {html_mod.escape(note.strip())}"

    await _notify_payment_topic_created(
        bot=bot,
        admin_user=admin_user,
        days=days,
        link_expires_str=expires_at.strftime("%d.%m.%Y"),
        gift_code=gift_code,
        note=note,
    )

    return (
        f"🎁 <b>Ссылка на подарок готова</b>\n\n"
        f"Доступ в клуб «{_CLUB_NAME}»: <b>{days_phrase}</b> "
        f"(с момента активации).\n"
        f"Ссылка одноразовая, активна <b>{link_phrase}</b> "
        f"(до {expires_at.strftime('%d.%m.%Y')})."
        f"{note_line}\n\n"
        f"🔗 <code>{html_mod.escape(link)}</code>\n\n"
        f"Отправьте её человеку — при переходе подарок активируется сам."
    )


async def activate_admin_gift_link(
    *,
    message: Message,
    user_storage,
    feature_manager,
    gift_code: str,
    bot=None,
    message_copier=None,
) -> None:
    """Активирует admin gift по коду из deep link."""
    user = message.from_user
    if user is None:
        return
    user_id = user.id
    bot = bot or message.bot
    code = (gift_code or "").strip().upper()
    if not code:
        await message.answer("❌ Некорректная ссылка на подарок.")
        return

    existing = await user_storage.get_admin_gift_code(code)
    if not existing:
        await message.answer(
            "❌ <b>Подарок не найден</b>\n\n"
            "Проверьте ссылку или попросите новую у того, кто её отправил.",
            parse_mode=ParseMode.HTML,
        )
        return

    status = existing.get("status") or ""
    if status == "used":
        activated_at = existing.get("activated_at")
        when = (
            activated_at.strftime("%d.%m.%Y")
            if activated_at
            else "ранее"
        )
        await message.answer(
            f"❌ <b>Эта ссылка уже использована</b>\n\n"
            f"Подарок активировали {when}.",
            parse_mode=ParseMode.HTML,
        )
        return
    if status == "revoked":
        await message.answer(
            "❌ <b>Ссылка отозвана</b>\n\nПопросите новую у администратора.",
            parse_mode=ParseMode.HTML,
        )
        return

    expires_at = existing.get("expires_at")
    if expires_at and _as_aware_utc(expires_at) < _utcnow():
        await message.answer(
            f"⏰ <b>Срок ссылки истёк</b>\n\n"
            f"Она была активна до {_as_aware_utc(expires_at).strftime('%d.%m.%Y')}.",
            parse_mode=ParseMode.HTML,
        )
        return

    claimed = await user_storage.claim_admin_gift_code(
        code, activated_by=user_id, now=_utcnow()
    )
    if not claimed:
        # гонка или истечение между проверкой и claim
        again = await user_storage.get_admin_gift_code(code)
        if again and again.get("status") == "used":
            await message.answer(
                "❌ Эта ссылка уже использована.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.answer(
                "⏰ Срок ссылки истёк или она больше недействительна.",
                parse_mode=ParseMode.HTML,
            )
        return

    days = int(claimed["days"])
    admin_id = int(claimed["created_by"])
    result = await user_storage.grant_admin_gift_license(
        user_id,
        days,
        admin_telegram_id=admin_id,
        origin="gift",
        meta_extra={
            "admin_gift_code": code,
            "via": "giftlink",
            "note": claimed.get("note"),
        },
    )
    if not result:
        await user_storage.release_admin_gift_code_claim(code)
        await message.answer(
            "❌ Не удалось выдать доступ. Попробуйте ещё раз позже "
            "или напишите в поддержку.",
            parse_mode=ParseMode.HTML,
        )
        return

    # допишем код в историю (meta уже записан grant'ом — отдельный append не нужен)
    new_expiry: datetime = result["new_expires_at"]
    expires_str = new_expiry.strftime("%d.%m.%Y")
    days_phrase = russian_days_phrase(days)

    await message.answer(
        f"🎉 <b>Подарок активирован!</b>\n\n"
        f"Вам открыт доступ в клуб «{_CLUB_NAME}» "
        f"на <b>{days_phrase}</b>.\n\n"
        f"📆 Действует до: <b>{expires_str}</b>\n\n"
        f"Дальше бот пришлёт приглашение в группу.",
        parse_mode=ParseMode.HTML,
    )

    club_group = feature_manager.get("club_group") if feature_manager else None
    if club_group:
        ok_invite = await club_group.send_admin_gift_invite(
            user_id, expires_str=expires_str
        )
        if not ok_invite:
            logger.warning(
                "admin gift link: invite failed uid=%s code=%s", user_id, code
            )

    await _notify_payment_topic_activated(
        bot=bot,
        user=user,
        target_user_id=user_id,
        days=days,
        expires_str=expires_str,
        was_extension=bool(result.get("was_extension")),
        admin_telegram_id=admin_id,
        gift_code=code,
        note=claimed.get("note"),
    )
    logger.info(
        "admin gift link activated code=%s uid=%s days=%s until=%s",
        code,
        user_id,
        days,
        expires_str,
    )


async def _notify_payment_topic_created(
    *,
    bot,
    admin_user: User,
    days: int,
    link_expires_str: str,
    gift_code: str,
    note: Optional[str],
) -> None:
    if not config.ADMIN_CHANNEL_ID:
        return
    note_line = ""
    if note and str(note).strip():
        note_line = f"\n📝 {html_mod.escape(str(note).strip())}"
    text = (
        f"🔗 <b>Создана админская gift-ссылка</b>\n\n"
        f"📆 +{days} дн. доступа с активации\n"
        f"⏳ Ссылка до: {link_expires_str}\n"
        f"🔑 Код: <code>{html_mod.escape(gift_code)}</code>"
        f"{note_line}\n"
        f"👮 Создал: {_format_admin_label(admin_user)}"
    )
    tid = config.PAYMENT_THREAD_ID
    await send_admin_html_message(
        bot,
        text,
        thread_id=tid if tid and tid > 0 else None,
    )


async def _notify_payment_topic_activated(
    *,
    bot,
    user: User,
    target_user_id: int,
    days: int,
    expires_str: str,
    was_extension: bool,
    admin_telegram_id: int,
    gift_code: str,
    note: Optional[str],
) -> None:
    if not config.ADMIN_CHANNEL_ID:
        return
    name = " ".join(
        p for p in (user.first_name or "", user.last_name or "") if p
    ).strip() or "Не указано"
    un = f"@{user.username}" if user.username else "нет username"
    title = (
        "🎁 <b>Админская gift-ссылка активирована (продление)</b>"
        if was_extension
        else "🎁 <b>Админская gift-ссылка активирована</b>"
    )
    note_line = ""
    if note and str(note).strip():
        note_line = f"\n📝 {html_mod.escape(str(note).strip())}"
    text = (
        f"{title}\n\n"
        f"👤 <b>Пользователь:</b> {html_mod.escape(name)}\n"
        f"🆔 <b>User ID:</b> <code>{target_user_id}</code>\n"
        f"📱 <b>Username:</b> {html_mod.escape(un)}\n\n"
        f"📆 +{days} дн., действует до: {expires_str}\n"
        f"🔑 Код: <code>{html_mod.escape(gift_code)}</code>"
        f"{note_line}\n"
        f"👮 Ссылку создал: <code>{admin_telegram_id}</code>"
    )
    tid = config.PAYMENT_THREAD_ID
    await send_admin_html_message(
        bot,
        text,
        thread_id=tid if tid and tid > 0 else None,
    )
