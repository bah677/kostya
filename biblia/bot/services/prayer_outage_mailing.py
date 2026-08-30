"""Черновик рассылки после тех. сбоев → «напишите снова» + кнопка молитвы."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from storage.mailing_storage import MailingStorage
from storage.user_storage import UserStorage

logger = logging.getLogger(__name__)

OUTAGE_MAILING_TEXT = (
    "Извините за неудобства 🙏\n\n"
    "В последние сутки у нас были технические сбои — "
    "иногда ответ мог не дойти, оборваться или прийти не полностью.\n\n"
    "Сейчас всё уже исправлено. Если ваш вопрос остался без ответа — "
    "просто <b>напишите боту ещё раз</b>, как обычно.\n\n"
    "А если нужна голосовая молитва — воспользуйтесь кнопкой ниже."
)

OUTAGE_MAILING_BUTTON = {
    "text": "🙏 Голосовая молитва",
    "style": "success",
    "callback": "prayer_start",
}


async def _admin_recipient_ids(
    storage: UserStorage, *, super_admin_id: int
) -> List[int]:
    ids: List[int] = []
    try:
        for row in await storage.list_telegram_admin_ids():
            tid = int(row.get("telegram_user_id") or 0)
            if tid > 0:
                ids.append(tid)
    except Exception as e:
        logger.warning("list_telegram_admin_ids: %s", e)
    if super_admin_id > 0:
        ids.append(super_admin_id)
    return sorted(set(ids))


async def create_prayer_outage_retry_mailing_draft(
    storage: UserStorage,
    mstore: MailingStorage,
    *,
    created_by: int,
    super_admin_id: int,
    hours: int = 24,
) -> Tuple[Optional[int], int, int, str]:
    """
    Создаёт planned-кампанию + аудиторию.
    Returns: (campaign_id, audience_total, base_affected_count, preview_html)
    """
    base_ids = await storage.list_bot_tech_affected_user_ids(hours=hours)
    admin_ids = await _admin_recipient_ids(storage, super_admin_id=super_admin_id)
    audience = sorted(set(base_ids) | set(admin_ids))
    if not audience:
        preview = (
            f"За последние <b>{hours}</b> ч никого не нашли "
            f"(инциденты в БД, тексты ошибок в messages, unlock pending).\n\n"
            "Рассылка не создана."
        )
        return None, 0, len(base_ids), preview

    name = f"После тех. сбоев {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    scheduled_at = datetime.now(timezone.utc) + timedelta(days=7)
    cid = await mstore.create_campaign(
        {
            "name": name,
            "text": OUTAGE_MAILING_TEXT,
            "parse_mode": "HTML",
            "scheduled_at": scheduled_at,
            "has_ref_link": False,
            "buttons": [OUTAGE_MAILING_BUTTON],
            "created_by": int(created_by),
            "media_type": None,
            "media_file_id": None,
            "attachments": None,
        }
    )
    if not cid:
        return None, 0, len(base_ids), "❌ Не удалось создать кампанию в БД."

    added = await mstore.add_audience_batch(int(cid), audience)
    btn = OUTAGE_MAILING_BUTTON
    preview = (
        f"📧 <b>Черновик рассылки</b> <code>{cid}</code>\n"
        f"Тема: после технических сбоев (ответ / молитва)\n"
        f"Окно: последние <b>{hours}</b> ч\n"
        f"Получателей: <b>{added}</b> "
        f"(затронутые {len(base_ids)} + админы {len(admin_ids)})\n"
        f"Кнопка: <b>{btn['text']}</b> → <code>{btn['callback']}</code>\n"
        f"Запуск: сразу после «Запустить» (суперадмин)\n\n"
        f"——— текст ——-\n\n"
        f"{OUTAGE_MAILING_TEXT}"
    )
    return int(cid), added, len(base_ids), preview


def outage_mailing_preview_keyboard(campaign_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Запустить рассылку",
                    callback_data=f"mdraft_ok_{campaign_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=f"mdraft_no_{campaign_id}",
                ),
            ]
        ]
    )
