"""Выдача партий подарочной волны + пороги нагрузки."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from aiogram.enums import ParseMode

from bot.texts import ru_gift_wave as txt
from bot.utils.admin_channel import send_admin_html_message
from config import config, russian_days_phrase

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")


async def is_air_day_today(user_storage) -> bool:
    """Эфирный день по расписанию клуба — партии не выдаём."""
    try:
        today = datetime.now(MSK).date()
        start = datetime(today.year, today.month, today.day, tzinfo=MSK)
        end = start + timedelta(days=1)
        async with user_storage.pool.acquire() as conn:
            n = await conn.fetchval(
                """
                SELECT COUNT(*)::int
                FROM club_schedule_events
                WHERE starts_at >= $1 AND starts_at < $2
                  AND COALESCE(is_cancelled, FALSE) = FALSE
                """,
                start,
                end,
            )
            return bool(n and int(n) > 0)
    except Exception as e:
        logger.warning("is_air_day_today: %s", e)
        return False


async def evaluate_wave_thresholds(
    user_storage, wave_id: int
) -> Tuple[int, List[str]]:
    """
    Возвращает (число нарушений, детали).
    Смотрит последнюю выданную партию (granted_at за последний batch).
    """
    wave = await user_storage.get_gift_wave(wave_id)
    if not wave or not wave.get("last_batch_at"):
        return 0, []

    last_batch = wave["last_batch_at"]
    if last_batch.tzinfo is None:
        last_batch = last_batch.replace(tzinfo=MSK)
    violations: List[str] = []

    try:
        async with user_storage.pool.acquire() as conn:
            # новички последней партии
            newcomers = await conn.fetch(
                """
                SELECT user_id, granted_at, joined_at, first_msg_at, first_msg_id, status
                FROM gift_wave_member
                WHERE wave_id = $1
                  AND granted_at IS NOT NULL
                  AND granted_at >= $2
                """,
                wave_id,
                last_batch - timedelta(minutes=5),
            )
            if not newcomers:
                return 0, []

            n = len(newcomers)
            # доля заговоривших за 48ч среди вошедших
            joined = [r for r in newcomers if r["joined_at"]]
            spoke_ok = 0
            for r in joined:
                if not r["first_msg_at"] or not r["joined_at"]:
                    continue
                delta = r["first_msg_at"] - r["joined_at"]
                if delta <= timedelta(hours=48):
                    spoke_ok += 1
            if joined:
                spoke_share = spoke_ok / len(joined)
                if spoke_share < 0.50:
                    violations.append(
                        f"заговорили за 48ч {spoke_share:.0%} (<50%)"
                    )

            # ответы на первое сообщение: медиана и доля
            reply_minutes: List[float] = []
            got_reply = 0
            with_first_msg = [r for r in newcomers if r["first_msg_id"]]
            for r in with_first_msg:
                mid = int(r["first_msg_id"])
                uid = int(r["user_id"])
                reply = await conn.fetchrow(
                    """
                    SELECT m.created_at
                    FROM messages m
                    WHERE m.chat_id = $1
                      AND m.reply_to_message_id = $2
                      AND m.user_id IS DISTINCT FROM $3
                      AND COALESCE(
                            (m.raw_data->'message_thread_id')::text,
                            ''
                          ) IS DISTINCT FROM $2::text
                    ORDER BY m.created_at ASC
                    LIMIT 1
                    """,
                    int(config.CLUB_GROUP_ID),
                    mid,
                    uid,
                )
                # упрощённо: reply_to_message_id колонка
                if not reply:
                    reply = await conn.fetchrow(
                        """
                        SELECT created_at
                        FROM messages
                        WHERE chat_id = $1
                          AND reply_to_message_id = $2
                          AND user_id IS DISTINCT FROM $3
                        ORDER BY created_at ASC
                        LIMIT 1
                        """,
                        int(config.CLUB_GROUP_ID),
                        mid,
                        uid,
                    )
                if reply and r["first_msg_at"]:
                    got_reply += 1
                    delta = reply["created_at"] - r["first_msg_at"]
                    reply_minutes.append(delta.total_seconds() / 60.0)

            if with_first_msg:
                share = got_reply / len(with_first_msg)
                if share < 0.70:
                    violations.append(f"получили ответ {share:.0%} (<70%)")
                if reply_minutes:
                    reply_minutes.sort()
                    med = reply_minutes[len(reply_minutes) // 2]
                    if med > 60:
                        violations.append(f"медиана ответа {med:.0f} мин (>60)")

            # выходы за 7 дней — по club_member_cache / left events упрощённо
            week_ago = datetime.now(MSK) - timedelta(days=7)
            left_n = await conn.fetchval(
                """
                SELECT COUNT(*)::int
                FROM gift_wave_member gwm
                WHERE gwm.wave_id = $1
                  AND gwm.joined_at IS NOT NULL
                  AND gwm.joined_at >= $2
                  AND NOT EXISTS (
                      SELECT 1 FROM club_group_member_cache c
                      WHERE c.user_id = gwm.user_id
                  )
                """,
                wave_id,
                week_ago,
            )
            joined_week = await conn.fetchval(
                """
                SELECT COUNT(*)::int
                FROM gift_wave_member
                WHERE wave_id = $1
                  AND joined_at IS NOT NULL
                  AND joined_at >= $2
                """,
                wave_id,
                week_ago,
            )
            if joined_week and joined_week > 0:
                left_share = (left_n or 0) / joined_week
                if left_share > 0.05:
                    violations.append(f"выходы {left_share:.0%} (>5%)")

    except Exception as e:
        logger.error("evaluate_wave_thresholds: %s", e, exc_info=True)
        return 0, []

    return len(violations), violations


async def grant_wave_batch(
    *,
    user_storage,
    bot: "Bot",
    feature_manager,
    wave_id: int,
) -> Dict[str, Any]:
    wave = await user_storage.get_gift_wave(wave_id)
    if not wave or wave.get("status") != "running":
        return {"ok": False, "reason": "not_running"}

    if await is_air_day_today(user_storage):
        return {"ok": False, "reason": "air_day"}

    # пороги только если уже была партия
    if wave.get("last_batch_at"):
        n_viol, details = await evaluate_wave_thresholds(user_storage, wave_id)
        if n_viol >= 2:
            await user_storage.set_gift_wave_status(
                wave_id, "paused", paused_reason="; ".join(details)
            )
            await send_admin_html_message(
                bot,
                txt.WAVE_ADMIN_THRESHOLD_HTML.format(
                    title=wave.get("title") or wave_id,
                    detail="; ".join(details),
                    wave_id=wave_id,
                ),
                thread_id=(
                    int(getattr(config, "GIFT_CAMPAIGN_ADMIN_TOPIC_ID", 0) or 0) or None
                ),
            )
            return {"ok": False, "reason": "paused_thresholds", "details": details}
        if n_viol == 1:
            new_interval = int(wave["interval_hours"]) * 2
            await user_storage.update_gift_wave_timing(
                wave_id, interval_hours=new_interval
            )
            logger.info(
                "wave %s: one threshold hit, interval -> %s h (%s)",
                wave_id,
                new_interval,
                details,
            )

        elapsed = datetime.now(MSK) - (
            wave["last_batch_at"].replace(tzinfo=MSK)
            if wave["last_batch_at"].tzinfo is None
            else wave["last_batch_at"].astimezone(MSK)
        )
        need = timedelta(hours=int(wave["interval_hours"]))
        if elapsed < need:
            return {
                "ok": False,
                "reason": "interval",
                "hours_left": (need - elapsed).total_seconds() / 3600,
            }

    batch_size = min(25, int(wave["batch_size"]))
    members = await user_storage.list_queued_wave_members(wave_id, limit=batch_size)
    if not members:
        await user_storage.set_gift_wave_status(wave_id, "done")
        return {"ok": True, "granted": 0, "done": True}

    gift_days = int(wave.get("gift_days") or 30)
    club_group = feature_manager.get("club_group") if feature_manager else None
    granted = 0
    defer_license = bool(wave.get("campaign")) or any(
        m.get("application_id") for m in members
    )

    try:
        await user_storage.log_interaction(
            user_id=0,
            event_category="gift_wave",
            event_type="wave_batch_granted",
            data={"wave_id": wave_id, "size": len(members), "defer_license": defer_license},
            source="gift_wave",
            outcome="success",
        )
    except Exception:
        pass

    for m in members:
        uid = int(m["user_id"])
        expires_str = ""
        days_phrase = russian_days_phrase(gift_days)

        if not defer_license:
            result = await user_storage.grant_admin_gift_license(
                uid,
                gift_days,
                admin_telegram_id=0,
                origin="gift",
                wave_id=wave_id,
            )
            if not result:
                logger.error("wave grant failed uid=%s wave=%s", uid, wave_id)
                continue
            expires_str = result["new_expires_at"].strftime("%d.%m.%Y")

        await user_storage.mark_wave_member_granted(wave_id, uid)

        invite_ok = False
        if club_group:
            try:
                if defer_license:
                    invite_ok = await club_group.send_gift_ticket_invite(uid)
                else:
                    invite_ok = await club_group.send_admin_gift_invite(
                        uid, expires_str=expires_str
                    )
            except Exception as e:
                logger.error("wave invite uid=%s: %s", uid, e)

        if not invite_ok and not defer_license:
            try:
                await bot.send_message(
                    uid,
                    txt.WAVE_GIFT_DM_HTML.format(
                        days_phrase=days_phrase,
                        expires_str=expires_str,
                        invite_footer="Открой /club — там будет ссылка в группу.",
                    ),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.error("wave DM uid=%s: %s", uid, e)
        elif not invite_ok and defer_license:
            try:
                from bot.texts import ru_gift_application as ga_txt

                user = await user_storage.get_user(uid)
                name = (user or {}).get("first_name")
                await bot.send_message(
                    uid,
                    ga_txt.T16_HTML.format(name_line=ga_txt.t16_name_line(name)),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.error("wave T16 fallback uid=%s: %s", uid, e)

        granted += 1
        try:
            await user_storage.log_interaction(
                user_id=uid,
                event_category="gift_wave",
                event_type="wave_gift_granted",
                data={"wave_id": wave_id, "defer_license": defer_license},
                source="gift_wave",
                outcome="success",
            )
        except Exception:
            pass

    await user_storage.touch_wave_last_batch(wave_id)
    remaining = await user_storage.list_queued_wave_members(wave_id, limit=1)
    if not remaining:
        await user_storage.set_gift_wave_status(wave_id, "done")
    return {"ok": True, "granted": granted, "done": not remaining}
