"""Очередь и алерты для ref_key и touch_key без псевдонима."""

from __future__ import annotations

import html as html_mod
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.utils.admin_channel import send_admin_html_message
from config import config

if TYPE_CHECKING:
    from aiogram import Bot
    from bot.services.attribution_touch import ParsedTouch
    from storage.user_storage import UserStorage

logger = logging.getLogger(__name__)

_GARBAGE_REF_KEYS = frozenset(
    {
        "id",
        "funnel",
        "ref_id",
        "ref_funnel",
    }
)

RK_CB_REGISTER = "rk:reg:"
RK_CB_DISMISS = "rk:ign:"
RK_CB_TYPE_PREFIX = "rk:typ:"
RK_CB_TYPE_SKIP = "rk:typ:skip"

TK_CB_REGISTER = "tk:reg:"
TK_CB_DISMISS = "tk:ign:"
TK_CB_TYPE_PREFIX = "tk:typ:"
TK_CB_TYPE_SKIP = "tk:typ:skip"


def is_garbage_ref_key(ref_key: str) -> bool:
    k = (ref_key or "").strip().lower()
    if not k:
        return True
    if k in _GARBAGE_REF_KEYS:
        return True
    return False


def ref_key_callback_token(ref_key: str) -> str:
    """Безопасный суффикс callback_data (лимит Telegram 64 байта)."""
    key = (ref_key or "").strip()
    if len(f"{RK_CB_REGISTER}{key}") <= 64:
        return key
    return key[:40]


def parse_ref_key_callback(data: str, prefix: str) -> Optional[str]:
    if not data or not data.startswith(prefix):
        return None
    token = data[len(prefix) :].strip()
    return token or None


async def resolve_ref_key_token(storage: "UserStorage", token: str) -> Optional[str]:
    """Полный ref_key по callback-токену (в т.ч. усечённому)."""
    t = (token or "").strip()
    if not t:
        return None
    if await storage.ref_key_exists(t):
        return t
    pending = await storage.list_ref_key_pending(include_dismissed=True, limit=500)
    exact = [r["ref_key"] for r in pending if r["ref_key"] == t]
    if len(exact) == 1:
        return exact[0]
    prefix = [r["ref_key"] for r in pending if r["ref_key"].startswith(t)]
    if len(prefix) == 1:
        return prefix[0]
    return t


async def sync_orphan_ref_keys_to_pending(storage: "UserStorage") -> int:
    """Подтягивает сирот из attribution_touches в очередь (миграция / старт бота)."""
    try:
        async with storage.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT at.ref_key, MIN(at.touch_key) AS sample_touch_key
                FROM attribution_touches at
                WHERE at.ref_key IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM ref_keys rk WHERE rk.ref_key = at.ref_key
                  )
                GROUP BY at.ref_key
                """
            )
    except Exception as e:
        logger.error("sync_orphan_ref_keys_to_pending: %s", e)
        return 0
    added = 0
    for row in rows:
        ref_key = str(row["ref_key"] or "").strip()
        if not ref_key:
            continue
        if not await storage.should_queue_ref_key_for_naming(ref_key):
            continue
        if await storage.upsert_ref_key_pending(
            ref_key, row.get("sample_touch_key")
        ):
            added += 1
    return added


async def maybe_queue_ref_key_from_touch(
    storage: "UserStorage",
    touch: "ParsedTouch",
) -> bool:
    """Ставит ref_key в очередь; True если ключ новый в pending."""
    ref_key = (touch.ref_key or "").strip()
    if not ref_key:
        return False
    if not hasattr(storage, "should_queue_ref_key_for_naming"):
        return False
    if not await storage.should_queue_ref_key_for_naming(ref_key):
        return False
    sample = touch.touch_key if touch.touch_key != f"ref_{ref_key}" else None
    return await storage.upsert_ref_key_pending(ref_key, sample)


async def sync_orphan_touch_keys_to_pending(storage: "UserStorage") -> int:
    """Колбэки/promo touch_key из attribution без псевдонима."""
    try:
        async with storage.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT at.touch_key
                FROM attribution_touches at
                WHERE at.touch_key IS NOT NULL
                  AND at.ref_key IS NULL
                  AND at.touch_key NOT LIKE 'ref_%'
                  AND at.touch_key NOT LIKE 'payment_select_%'
                  AND at.touch_key NOT LIKE 'payment_currency_rub_%'
                  AND at.touch_key NOT LIKE 'payment_currency_usd_%'
                  AND NOT EXISTS (
                      SELECT 1 FROM touch_key_labels tkl
                      WHERE tkl.touch_key = at.touch_key
                  )
                """
            )
    except Exception as e:
        logger.error("sync_orphan_touch_keys_to_pending: %s", e)
        return 0
    added = 0
    for row in rows:
        touch_key = str(row["touch_key"] or "").strip()
        if not touch_key:
            continue
        if not await storage.should_queue_touch_key_for_naming(touch_key):
            continue
        if await storage.upsert_touch_key_pending(touch_key):
            added += 1
    return added


async def maybe_queue_touch_key_from_touch(
    storage: "UserStorage",
    touch: "ParsedTouch",
) -> bool:
    touch_key = (touch.touch_key or "").strip()
    if not touch_key:
        return False
    if not hasattr(storage, "should_queue_touch_key_for_naming"):
        return False
    if not await storage.should_queue_touch_key_for_naming(
        touch_key, touch.ref_key
    ):
        return False
    return await storage.upsert_touch_key_pending(touch_key)


async def maybe_alert_new_marketing_touch(
    storage: "UserStorage",
    bot: Optional["Bot"],
    touch: "ParsedTouch",
) -> None:
    """После касания: очередь ref_key и/или touch_key + разовый алерт."""
    if bot is None:
        return
    ref_new = await maybe_queue_ref_key_from_touch(storage, touch)
    touch_new = await maybe_queue_touch_key_from_touch(storage, touch)

    if ref_new:
        ref_key = (touch.ref_key or "").strip()
        row = next(
            (
                r
                for r in await storage.list_ref_key_pending_for_notify(limit=100)
                if r["ref_key"] == ref_key
            ),
            None,
        )
        if row:
            await _send_pending_ref_key_alert(bot, storage, row)

    if touch_new:
        touch_key = (touch.touch_key or "").strip()
        row = next(
            (
                r
                for r in await storage.list_touch_key_pending_for_notify(limit=100)
                if r["touch_key"] == touch_key
            ),
            None,
        )
        if row:
            await _send_pending_touch_key_alert(bot, storage, row)


async def maybe_alert_new_ref_key(
    storage: "UserStorage",
    bot: Optional["Bot"],
    touch: "ParsedTouch",
) -> None:
    """Обратная совместимость."""
    await maybe_alert_new_marketing_touch(storage, bot, touch)


async def flush_pending_ref_key_alerts(
    storage: "UserStorage",
    bot: Optional["Bot"],
    *,
    limit: int = 10,
) -> int:
    """Отправляет накопившиеся алерты (после рестарта бота)."""
    if bot is None:
        return 0
    sent = 0
    rows = await storage.list_ref_key_pending_for_notify(limit=limit)
    for row in rows:
        if await _send_pending_ref_key_alert(bot, storage, row):
            sent += 1
    touch_rows = await storage.list_touch_key_pending_for_notify(limit=limit)
    for row in touch_rows:
        if await _send_pending_touch_key_alert(bot, storage, row):
            sent += 1
    return sent


async def _send_pending_ref_key_alert(
    bot: "Bot",
    storage: "UserStorage",
    row: Dict[str, Any],
) -> bool:
    ref_key = str(row.get("ref_key") or "").strip()
    if not ref_key:
        return False
    token = ref_key_callback_token(ref_key)
    count = int(row.get("touch_count") or 1)
    sample = (row.get("sample_touch_key") or "").strip()

    lines = [
        "🏷 <b>Новый ref-ключ без псевдонима</b>",
        "",
        f"Ключ: <code>{html_mod.escape(ref_key)}</code>",
        f"Касаний: <b>{count}</b>",
    ]
    if sample:
        lines.append(f"Пример: <code>{html_mod.escape(sample)}</code>")
    lines.extend(
        [
            "",
            "Добавьте псевдоним в <code>ref_keys</code>, чтобы он отображался "
            "в уведомлениях об оплате и отчётах.",
            "",
            f"Команда: <code>/ref_key {html_mod.escape(ref_key)}</code>",
        ]
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Задать псевдоним",
                    callback_data=f"{RK_CB_REGISTER}{token}",
                ),
                InlineKeyboardButton(
                    text="Игнор",
                    callback_data=f"{RK_CB_DISMISS}{token}",
                ),
            ]
        ]
    )

    thread_id = config.SUPPORT_THREAD_ID if config.SUPPORT_THREAD_ID > 0 else None
    try:
        await send_admin_html_message(
            bot,
            "\n".join(lines),
            message_thread_id=thread_id,
            reply_markup=kb,
        )
        await storage.mark_ref_key_pending_notified(ref_key)
        return True
    except Exception as e:
        logger.error("ref_key pending alert %s: %s", ref_key, e)
        return False


async def _send_pending_touch_key_alert(
    bot: "Bot",
    storage: "UserStorage",
    row: Dict[str, Any],
) -> bool:
    pending_id = int(row["id"])
    touch_key = str(row.get("touch_key") or "").strip()
    if not touch_key:
        return False
    count = int(row.get("touch_count") or 1)

    lines = [
        "🏷 <b>Новый колбэк без псевдонима</b>",
        "",
        f"Ключ: <code>{html_mod.escape(touch_key)}</code>",
        f"Касаний: <b>{count}</b>",
        "",
        "Задайте псевдоним — он появится в уведомлениях об оплате "
        "(источник оплаты).",
        "",
        f"Команда: <code>/touch_key {html_mod.escape(touch_key[:80])}</code>",
    ]

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Задать псевдоним",
                    callback_data=f"{TK_CB_REGISTER}{pending_id}",
                ),
                InlineKeyboardButton(
                    text="Игнор",
                    callback_data=f"{TK_CB_DISMISS}{pending_id}",
                ),
            ]
        ]
    )

    thread_id = config.SUPPORT_THREAD_ID if config.SUPPORT_THREAD_ID > 0 else None
    try:
        await send_admin_html_message(
            bot,
            "\n".join(lines),
            message_thread_id=thread_id,
            reply_markup=kb,
        )
        await storage.mark_touch_key_pending_notified(pending_id)
        return True
    except Exception as e:
        logger.error("touch_key pending alert id=%s: %s", pending_id, e)
        return False


def format_pending_marketing_keys_html(
    ref_rows: List[Dict[str, Any]],
    touch_rows: List[Dict[str, Any]],
) -> str:
    if not ref_rows and not touch_rows:
        return (
            "🏷 <b>Очередь маркетинговых ключей</b>\n\n"
            "Нет ключей без псевдонима. Каталог: <code>/ref_funnel</code>"
        )
    lines = [
        "🏷 <b>Очередь ключей без псевдонима</b>",
        "<i>ref — диплинки · touch — колбэки оплаты/promo</i>",
        "",
    ]
    if ref_rows:
        lines.append(f"<b>Ref ({len(ref_rows)})</b>")
        for row in ref_rows[:15]:
            key = html_mod.escape(str(row.get("ref_key") or ""))
            cnt = int(row.get("touch_count") or 0)
            notified = "✓" if row.get("admin_notified_at") else "·"
            lines.append(f"{notified} <code>{key}</code> · {cnt}")
        lines.append("")
    if touch_rows:
        lines.append(f"<b>Колбэки ({len(touch_rows)})</b>")
        for row in touch_rows[:15]:
            key = html_mod.escape(str(row.get("touch_key") or ""))
            cnt = int(row.get("touch_count") or 0)
            notified = "✓" if row.get("admin_notified_at") else "·"
            short = key if len(key) <= 48 else key[:45] + "…"
            lines.append(f"{notified} <code>{short}</code> · {cnt}")
    lines.append("")
    lines.append(
        "<i>/ref_key KEY · /touch_key CALLBACK · кнопки в алертах</i>"
    )
    return "\n".join(lines)


def pending_marketing_keys_keyboard(
    ref_rows: List[Dict[str, Any]],
    touch_rows: List[Dict[str, Any]],
) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    for row in ref_rows[:4]:
        key = str(row.get("ref_key") or "").strip()
        if not key:
            continue
        token = ref_key_callback_token(key)
        label = key if len(key) <= 24 else f"{key[:21]}…"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"ref {label}",
                    callback_data=f"{RK_CB_REGISTER}{token}",
                )
            ]
        )
    for row in touch_rows[:4]:
        pending_id = row.get("id")
        key = str(row.get("touch_key") or "").strip()
        if not pending_id or not key:
            continue
        label = key if len(key) <= 24 else f"{key[:21]}…"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"cb {label}",
                    callback_data=f"{TK_CB_REGISTER}{int(pending_id)}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def format_pending_ref_keys_html(rows: List[Dict[str, Any]]) -> str:
    return format_pending_marketing_keys_html(rows, [])


def pending_ref_keys_keyboard(rows: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    for row in rows[:8]:
        key = str(row.get("ref_key") or "").strip()
        if not key:
            continue
        token = ref_key_callback_token(key)
        label = key if len(key) <= 28 else f"{key[:25]}…"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"🏷 {label}",
                    callback_data=f"{RK_CB_REGISTER}{token}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def ref_key_type_keyboard(
    types: List[str], *, type_prefix: str = RK_CB_TYPE_PREFIX, skip_data: str = RK_CB_TYPE_SKIP
) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for i, t in enumerate(types[:6]):
        rows.append(
            [
                InlineKeyboardButton(
                    text=t[:48],
                    callback_data=f"{type_prefix}{i}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Без типа",
                callback_data=skip_data,
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
