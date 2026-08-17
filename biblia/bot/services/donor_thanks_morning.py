# bot/services/donor_thanks_morning.py
"""Утренняя благодарность донорам за прошлые квотные сутки (после фиксации лимита)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional, Set

from aiogram import Bot
from aiogram.enums import ParseMode

from bot.payments.currency_converter import CurrencyConverterService
from bot.services.prayer_voice_funding import PrayerVoiceFundingService
from bot.services.prayer_voice_quota import (
    quota_day_for,
    previous_quota_day,
    quota_window,
)
from bot.utils.admin_channel import send_admin_html_message
from config import config

logger = logging.getLogger(__name__)

SETTING_KEY_LAST_SENT = "donor_thanks_last_quota_day"

_THANKS_QUOTES = (
    "<blockquote>Каждый уделяй по расположению сердца, не с огорчением и не с "
    "принуждением; ибо доброхотно дающего любит Бог.\n\n"
    "<i>(2 Кор. 9:7)</i></blockquote>",
    "<blockquote>Блаженнее давать, нежели принимать.\n\n"
    "<i>(Деян. 20:35)</i></blockquote>",
    "<blockquote>Давайте, и дастся вам: мерою доброю, утрясённою, нагнетённою и "
    "переполненною отсыплют вам в лоно ваше.\n\n"
    "<i>(Лк. 6:38)</i></blockquote>",
    "<blockquote>Кто сеет скупо, тот скупо и пожнёт; а кто сеет щедро, тот щедро "
    "и пожнёт.\n\n"
    "<i>(2 Кор. 9:6)</i></blockquote>",
    "<blockquote>Благотворительная душа будет насыщена, и кто напояет других, "
    "тот и сам напоен будет.\n\n"
    "<i>(Притч. 11:25)</i></blockquote>",
)


@dataclass(frozen=True)
class DonorThanksRunResult:
    quota_day: date
    limit_slots: int
    donor_ids: List[int]
    admin_ids: List[int]
    recipient_ids: List[int]
    sent_ok: int
    sent_fail: int
    skipped: bool
    text_html: str


def pick_thanks_quote(*, on_day: Optional[date] = None) -> str:
    day = on_day or quota_day_for()
    return _THANKS_QUOTES[day.toordinal() % len(_THANKS_QUOTES)]


def build_donor_thanks_html(*, limit_slots: int, quote_html: Optional[str] = None) -> str:
    quote = quote_html or pick_thanks_quote()
    return (
        "🙏 <b>Спасибо, что поддерживаете проект!</b>\n\n"
        "Благодаря вашим пожертвованиям сегодня доступно "
        f"<b>{int(limit_slots)}</b> бесплатных голосовых молитв для всех.\n\n"
        "Своим взносом вы делаете большое дело не только для себя — "
        "вы помогаете людям, которые сейчас, возможно, в вопросе или "
        "в непростой ситуации. Для кого-то именно эта молитва станет ответом.\n\n"
        "Низкий поклон ❤️\n\n"
        f"{quote}"
    )


async def list_succeeded_donor_user_ids(
    user_storage,
    start: datetime,
    end: datetime,
) -> List[int]:
    try:
        async with user_storage.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT user_id
                  FROM payments
                 WHERE status = 'succeeded'
                   AND user_id > 0
                   AND completed_at >= $1
                   AND completed_at < $2
                 ORDER BY user_id
                """,
                start,
                end,
            )
        return [int(r["user_id"]) for r in rows]
    except Exception as e:
        logger.error("list_succeeded_donor_user_ids: %s", e, exc_info=True)
        return []


async def list_admin_recipient_ids(user_storage) -> List[int]:
    ids: Set[int] = set()
    try:
        rows = await user_storage.list_telegram_admin_ids()
        for r in rows or []:
            tid = int(r.get("telegram_user_id") or 0)
            if tid > 0:
                ids.add(tid)
    except Exception as e:
        logger.warning("list_admin_recipient_ids: %s", e)
    super_id = int(getattr(config, "SUPER_ADMIN_ID", 0) or 0)
    if super_id > 0:
        ids.add(super_id)
    return sorted(ids)


async def run_donor_thanks_morning(
    bot: Bot,
    user_storage,
    *,
    quota_day: Optional[date] = None,
    force: bool = False,
    also_admin_channel: bool = True,
    currency_converter: Optional[CurrencyConverterService] = None,
) -> DonorThanksRunResult:
    await user_storage.ensure_prayer_voice_quota_schema()
    day = quota_day or quota_day_for()
    if not force:
        last = (await user_storage.get_runtime_setting(SETTING_KEY_LAST_SENT, "")).strip()
        if last == day.isoformat():
            logger.info("donor thanks already sent for %s — skip", day)
            return DonorThanksRunResult(
                quota_day=day,
                limit_slots=0,
                donor_ids=[],
                admin_ids=[],
                recipient_ids=[],
                sent_ok=0,
                sent_fail=0,
                skipped=True,
                text_html="",
            )

    conv = currency_converter or CurrencyConverterService()
    funding = PrayerVoiceFundingService(user_storage, currency_converter=conv)
    period = await funding.ensure_period(day)
    limit_slots = int(period.limit_slots)

    prev = previous_quota_day(day)
    start, end = quota_window(prev)
    donor_ids = await list_succeeded_donor_user_ids(user_storage, start, end)
    admin_ids = await list_admin_recipient_ids(user_storage)
    recipients = sorted(set(donor_ids) | set(admin_ids))

    text = build_donor_thanks_html(
        limit_slots=limit_slots,
        quote_html=pick_thanks_quote(on_day=day),
    )

    ok = 0
    fail = 0
    for uid in recipients:
        try:
            await bot.send_message(uid, text, parse_mode=ParseMode.HTML)
            ok += 1
        except Exception as e:
            fail += 1
            logger.warning("donor thanks send uid=%s: %s", uid, e)
        await asyncio.sleep(0.04)

    if also_admin_channel:
        admin_extra = (
            f"{text}\n\n"
            f"📊 <i>авто · период {day.isoformat()} · доноров: {len(donor_ids)} · "
            f"слотов: {limit_slots} · доставлено: {ok}/{len(recipients)}</i>"
        )
        try:
            tid = getattr(config, "PAYMENT_THREAD_ID", None) or 0
            await send_admin_html_message(
                bot,
                admin_extra,
                thread_id=tid if tid and int(tid) > 0 else None,
            )
        except Exception as e:
            logger.warning("donor thanks admin channel: %s", e)

    await user_storage.set_runtime_setting(SETTING_KEY_LAST_SENT, day.isoformat())
    logger.info(
        "donor thanks day=%s limit=%s donors=%s admins=%s ok=%s fail=%s",
        day,
        limit_slots,
        len(donor_ids),
        len(admin_ids),
        ok,
        fail,
    )
    return DonorThanksRunResult(
        quota_day=day,
        limit_slots=limit_slots,
        donor_ids=donor_ids,
        admin_ids=admin_ids,
        recipient_ids=recipients,
        sent_ok=ok,
        sent_fail=fail,
        skipped=False,
        text_html=text,
    )
