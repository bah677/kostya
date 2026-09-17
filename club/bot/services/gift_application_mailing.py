"""Рассылка Т1 через стандартный механизм mailing_campaigns (черновик → mdraft)."""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Set
from zoneinfo import ZoneInfo

from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.texts import ru_gift_application as txt
from config import config
from storage.db.gift_application import CAMPAIGN_ID
from storage.mailing_storage import MailingStorage

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

COHORT_ORDER = ("K1", "K2", "K3")
COHORT_ALL = ("TEST", "K1", "K2", "K3")
PORTION_SIZE = 300
STAGE1_TICKETS = 50

COHORT_TITLES = {
    "TEST": "TEST — только админы",
    "K1": "К1 — 2+ дня в боте, активны за 60 дней, рег. 30+",
    "K2": "К2 — 2+ дня в боте, давно не писали, рег. 30+",
    "K3": "К3 — один день / без сообщений, рег. 30+",
}


async def _stage1_remaining_slots(user_storage) -> int:
    async with user_storage.get_connection() as conn:
        used = await conn.fetchval(
            """
            SELECT COUNT(*)::int
            FROM gift_wave_member gwm
            JOIN gift_wave gw ON gw.id = gwm.wave_id
            WHERE gwm.status IN ('granted', 'activated')
              AND gw.campaign = $1
              AND COALESCE(gwm.source, 'bot') IN ('bot', 'other')
            """,
            CAMPAIGN_ID,
        )
    return max(0, STAGE1_TICKETS - int(used or 0))


async def _queued_for_stage1(user_storage) -> int:
    by = await user_storage.count_queued_by_source(campaign=CAMPAIGN_ID)
    return int(by.get("bot") or 0) + int(by.get("other") or 0)


async def should_send_club_portion(user_storage) -> Dict[str, Any]:
    st = await user_storage.get_or_create_gift_campaign_state()
    if st.get("mailing_paused"):
        return {"ok": False, "reason": "paused"}
    if int(st.get("stage") or 1) > 1:
        return {"ok": False, "reason": "stage_done"}
    left = await _stage1_remaining_slots(user_storage)
    if left <= 0:
        return {"ok": False, "reason": "stage_full"}
    queued = await _queued_for_stage1(user_storage)
    if queued >= 2 * left:
        return {"ok": False, "reason": "enough_apps", "queued": queued, "left": left}
    return {"ok": True, "left": left, "queued": queued}


async def list_admin_recipient_ids(user_storage) -> List[int]:
    ids: Set[int] = set()
    try:
        for row in await user_storage.list_telegram_admin_ids():
            uid = int(row.get("telegram_user_id") or 0)
            if uid > 0:
                ids.add(uid)
    except Exception as e:
        logger.warning("list admins for gift mail: %s", e)
    sid = int(getattr(config, "SUPER_ADMIN_ID", 0) or 0)
    if sid > 0:
        ids.add(sid)
    return sorted(ids)


async def _resolve_cohort_user_ids(
    user_storage,
    *,
    cohort: str,
    limit: int,
) -> List[int]:
    c = cohort.upper()
    if c == "TEST":
        return await list_admin_recipient_ids(user_storage)

    raw = await user_storage.list_club_gift_cohort_candidates(
        cohort=c, limit=limit, campaign=CAMPAIGN_ID
    )
    # не звать тех, у кого сегодня ушёл followup
    out: List[int] = []
    for uid in raw:
        try:
            async with user_storage.get_connection() as conn:
                hit = await conn.fetchval(
                    """
                    SELECT 1 FROM followup_log
                    WHERE user_id = $1
                      AND COALESCE(delivered, TRUE) = TRUE
                      AND sent_at >= (NOW() AT TIME ZONE 'Europe/Moscow')::date
                                     AT TIME ZONE 'Europe/Moscow'
                    LIMIT 1
                    """,
                    uid,
                )
            if hit:
                continue
        except Exception:
            pass
        out.append(uid)
    return out


def _merge_with_admins(user_ids: Sequence[int], admin_ids: Sequence[int]) -> List[int]:
    seen: Set[int] = set()
    out: List[int] = []
    for uid in list(admin_ids) + list(user_ids):
        uid = int(uid)
        if uid <= 0 or uid in seen:
            continue
        seen.add(uid)
        out.append(uid)
    return out


def _t1_body() -> str:
    # В массовой рассылке одно письмо на всех — без персонального имени.
    return txt.T1_HTML.format(name_suffix="")


def _t1_buttons() -> List[Dict[str, str]]:
    return [{"text": txt.BTN_APPLY, "callback": txt.CB_APPLY}]


async def create_club_portion_draft(
    *,
    user_storage,
    bot,
    admin_id: int,
    cohort: Optional[str] = None,
    limit: int = PORTION_SIZE,
    force: bool = False,
) -> Dict[str, Any]:
    """Создаёт planned-кампанию mailing_campaigns и шлёт админу черновик с mdraft_*.

    Рассылка уходит штатным воркером после «Запустить рассылку».
    """
    c = (cohort or "").strip().upper() or None
    if c and c not in COHORT_ALL:
        return {"ok": False, "reason": "bad_cohort", "allowed": list(COHORT_ALL)}

    is_test = c == "TEST"
    if not is_test:
        check = await should_send_club_portion(user_storage)
        if not check.get("ok") and not force:
            return check

    order = list(COHORT_ORDER)
    if c:
        order = [c]

    admin_ids = await list_admin_recipient_ids(user_storage)
    used_cohort: Optional[str] = None
    cohort_ids: List[int] = []

    for name in order:
        if name == "TEST":
            cohort_ids = []
            used_cohort = "TEST"
            break
        cohort_ids = await _resolve_cohort_user_ids(
            user_storage, cohort=name, limit=limit
        )
        if cohort_ids or name == c:
            used_cohort = name
            break

    if used_cohort is None:
        return {"ok": False, "reason": "empty_cohort"}

    recipients = _merge_with_admins(cohort_ids, admin_ids)
    if not recipients:
        return {"ok": False, "reason": "no_recipients", "cohort": used_cohort}

    body = _t1_body()
    buttons = _t1_buttons()
    # mailing_campaigns.scheduled_at — timestamp WITHOUT time zone (как /new_mailing)
    scheduled_at = datetime.utcnow() + timedelta(days=7)
    title = f"gift-2026-09 Т1 {used_cohort} {datetime.now(MSK).strftime('%m-%d %H:%M')}"

    mstore = MailingStorage(user_storage)
    cid = await mstore.create_campaign(
        {
            "name": title,
            "text": body,
            "parse_mode": "HTML",
            "scheduled_at": scheduled_at,
            "has_ref_link": False,
            "buttons": buttons,
            "created_by": admin_id,
            "media_type": None,
            "media_file_id": None,
            "attachments": None,
        }
    )
    if not cid:
        return {"ok": False, "reason": "create_failed"}

    added = await mstore.add_audience_batch(int(cid), recipients)

    # Резервируем аудиторию, чтобы следующая порция их не взяла (при cancel — снимем).
    if used_cohort != "TEST":
        for uid in cohort_ids:
            try:
                await user_storage.record_gift_mailing_sent(
                    uid, cohort=used_cohort, stage=1, delivery="sent"
                )
            except Exception:
                pass

    await user_storage.update_gift_campaign_state(club_cohort=used_cohort)

    cohort_title = COHORT_TITLES.get(used_cohort, used_cohort)
    preview_meta = (
        f"📧 <b>Черновик рассылки подарочной волны</b>\n"
        f"Кампания: <code>{cid}</code>\n"
        f"Когорта: <b>{html.escape(used_cohort)}</b> — {html.escape(cohort_title)}\n"
        f"Получателей: <b>{added}</b>"
        f" (когорта {len(cohort_ids)} + админы {len(admin_ids)}, "
        f"уникальных {len(recipients)})\n"
        f"Механизм: стандартные <code>mailing_campaigns</code> "
        f"(после «Запустить» уйдёт воркером).\n\n"
        f"——— текст получателям ——-\n\n"
        f"{body}"
    )

    # Кнопки кампании (как у получателя) + запуск/отмена черновика
    kb_rows: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=txt.BTN_APPLY, callback_data=txt.CB_APPLY)],
        [
            InlineKeyboardButton(
                text="✅ Запустить рассылку",
                callback_data=f"mdraft_ok_{cid}",
            ),
            InlineKeyboardButton(
                text="❌ Отменить",
                callback_data=f"mdraft_no_{cid}",
            ),
        ],
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    try:
        await bot.send_message(
            admin_id,
            preview_meta,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error("gift portion preview to admin %s: %s", admin_id, e)
        return {
            "ok": False,
            "reason": "preview_failed",
            "campaign_id": cid,
            "error": str(e),
        }

    return {
        "ok": True,
        "campaign_id": cid,
        "cohort": used_cohort,
        "cohort_title": cohort_title,
        "recipients": len(recipients),
        "cohort_n": len(cohort_ids),
        "admins_n": len(admin_ids),
        "added": added,
    }


async def release_gift_mailing_reservation(
    user_storage, *, campaign_id: int
) -> int:
    """При отмене черновика — вернуть когорту в пул (удалить gift_mailing_sent)."""
    try:
        mstore = MailingStorage(user_storage)
        camp = await mstore.get_campaign(campaign_id)
        if not camp:
            return 0
        name = str(camp.get("name") or "")
        if not name.startswith("gift-2026-09"):
            return 0
        async with user_storage.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT user_id FROM mailing_audience WHERE campaign_id = $1
                """,
                campaign_id,
            )
            uids = [int(r["user_id"]) for r in rows]
            if not uids:
                return 0
            # не трогаем админов — они не в gift_mailing_sent как когорта
            result = await conn.execute(
                """
                DELETE FROM gift_mailing_sent
                WHERE campaign = $1
                  AND user_id = ANY($2::bigint[])
                  AND cohort <> 'TEST'
                """,
                CAMPAIGN_ID,
                uids,
            )
            try:
                return int(result.split()[-1])
            except Exception:
                return 0
    except Exception as e:
        logger.warning("release_gift_mailing_reservation #%s: %s", campaign_id, e)
        return 0
