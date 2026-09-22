"""Алерты подарочной волны в админ-топик (4 типа)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from bot.services.gift_application_mailing import (
    STAGE1_TICKETS,
    should_send_club_portion,
)
from bot.services.gift_application_select import DRAW_SLOTS, SCORE_SLOTS
from bot.utils.admin_channel import send_admin_html_message
from config import config
from storage.db.gift_application import CAMPAIGN_ID

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

ALERT_NEED_PORTION = "need_portion"
ALERT_ENOUGH_APPS = "enough_apps"
ALERT_READY_WAVE = "ready_wave"
ALERT_HIGH_BLOCKS = "high_blocks"

BLOCK_RATE_LIMIT = 0.05
# минимум заявок в очереди бота, чтобы звать админа на wave
WAVE_QUEUE_MIN = DRAW_SLOTS + SCORE_SLOTS  # 25
# не дублировать тот же fingerprint чаще
ALERT_COOLDOWN = timedelta(hours=12)


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=MSK)
        return dt
    except Exception:
        return None


def _alerts_map(state: Dict[str, Any]) -> Dict[str, Any]:
    raw = state.get("alerts_json")
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


async def _save_alert(
    user_storage,
    *,
    state: Dict[str, Any],
    kind: str,
    fingerprint: str,
) -> None:
    data = _alerts_map(state)
    data[kind] = {
        "fp": fingerprint,
        "at": datetime.now(MSK).isoformat(),
    }
    await user_storage.update_gift_campaign_state(
        alerts_json=data,
    )


def _should_skip(
    state: Dict[str, Any],
    *,
    kind: str,
    fingerprint: str,
    now: datetime,
) -> bool:
    prev = _alerts_map(state).get(kind) or {}
    if prev.get("fp") == fingerprint:
        at = _parse_iso(prev.get("at"))
        if at and now - at < ALERT_COOLDOWN:
            return True
        # тот же fp после cooldown — всё равно не спамим, пока fp не сменился
        return True
    return False


async def _topic_id() -> Optional[int]:
    tid = int(getattr(config, "GIFT_CAMPAIGN_ADMIN_TOPIC_ID", 0) or 0)
    return tid if tid > 0 else None


async def _send(bot, text: str) -> bool:
    tid = await _topic_id()
    if tid is None:
        logger.warning("gift alert skip: GIFT_CAMPAIGN_ADMIN_TOPIC_ID not set")
        return False
    return await send_admin_html_message(bot, text, thread_id=tid)


async def _next_wave_index(user_storage) -> Tuple[int, Optional[Dict[str, Any]]]:
    """Следующая волна 1..6, которую ещё не запускали (нет running/done)."""
    async with user_storage.get_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT wave_index, status, id, last_batch_at, created_at
            FROM gift_wave
            WHERE campaign = $1 AND wave_index IS NOT NULL
            ORDER BY wave_index, id DESC
            """,
            CAMPAIGN_ID,
        )
    by_idx: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        idx = int(r["wave_index"])
        if idx not in by_idx:
            by_idx[idx] = dict(r)
    for i in range(1, 7):
        w = by_idx.get(i)
        if not w:
            return i, None
        if w.get("status") in ("draft",):
            return i, w
        if w.get("status") in ("running", "paused"):
            return i, w
        # done / другое — смотрим дальше
    return 0, None


async def resolve_campaign_wave_button(
    user_storage,
) -> Optional[Dict[str, Any]]:
    """Кнопка под статусом: розыгрыш или выдача партии.

    Возвращает ``{wave_index, wave_id|None, mode: draw|grant, label}`` или None.
    """
    from bot.services.gift_application_eligibility import count_remaining_tickets
    from bot.texts import ru_gift_application as ga_txt

    st = await user_storage.get_or_create_gift_campaign_state()
    if st.get("finished_at") or st.get("waves_paused"):
        return None
    left = await count_remaining_tickets(user_storage)
    if left <= 0:
        return None

    next_idx, wave_row = await _next_wave_index(user_storage)
    if next_idx <= 0:
        return None

    wave_id = int(wave_row["id"]) if wave_row else None
    status = (wave_row or {}).get("status")

    # Уже отобрали — осталось выдать очередь gift_wave_member
    if wave_id and status in ("running", "paused"):
        queued_members = await user_storage.list_queued_wave_members(wave_id, limit=1)
        if queued_members:
            return {
                "wave_index": next_idx,
                "wave_id": wave_id,
                "mode": "grant",
                "label": ga_txt.BTN_GRANT_WAVE.format(n=next_idx),
            }

    # Новый отбор: есть заявки в очереди анкеты
    if status in ("running", "paused", "done"):
        return None
    by_src = await user_storage.count_queued_by_source()
    apps_queued = sum(int(v) for v in (by_src or {}).values())
    # Минимум как в алерте «пора отбор» — половина слотов волны
    if apps_queued < max(15, WAVE_QUEUE_MIN // 2):
        return None
    return {
        "wave_index": next_idx,
        "wave_id": wave_id,
        "mode": "draw",
        "label": ga_txt.BTN_DRAW_WAVE.format(n=next_idx),
    }


async def _hours_since_campaign_start(user_storage) -> Optional[float]:
    st = await user_storage.get_or_create_gift_campaign_state()
    started = st.get("started_at")
    if not started:
        return None
    if getattr(started, "tzinfo", None) is None:
        started = started.replace(tzinfo=MSK)
    else:
        started = started.astimezone(MSK)
    return (datetime.now(MSK) - started).total_seconds() / 3600.0


async def _latest_gift_mailing_block_rate(
    user_storage,
) -> Optional[Dict[str, Any]]:
    async with user_storage.get_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, name, status,
                   COALESCE(sent_count, 0)::int AS sent_count,
                   COALESCE(blocked_count, 0)::int AS blocked_count,
                   COALESCE(failed_count, 0)::int AS failed_count,
                   finished_at, updated_at
            FROM mailing_campaigns
            WHERE name LIKE 'gift-2026-09%'
              AND status = 'completed'
              AND COALESCE(sent_count, 0) > 0
            ORDER BY COALESCE(finished_at, updated_at) DESC NULLS LAST, id DESC
            LIMIT 1
            """
        )
    if not row:
        return None
    sent = int(row["sent_count"] or 0)
    blocked = int(row["blocked_count"] or 0)
    if sent <= 0:
        return None
    return {
        "campaign_id": int(row["id"]),
        "name": row["name"],
        "sent": sent,
        "blocked": blocked,
        "rate": blocked / sent,
    }


async def run_gift_campaign_alerts(*, user_storage, bot) -> Dict[str, Any]:
    """Проверяет условия и шлёт до 4 типов алертов в топик. Идемпотентно."""
    sent: Dict[str, bool] = {}
    st = await user_storage.get_or_create_gift_campaign_state()
    if st.get("finished_at"):
        return {"ok": True, "skipped": "finished", "sent": sent}
    if not st.get("started_at"):
        return {"ok": True, "skipped": "not_started", "sent": sent}

    now = datetime.now(MSK)
    check = await should_send_club_portion(user_storage)
    queued = int(check.get("queued") or 0)
    left = int(check.get("left") or STAGE1_TICKETS)

    # --- 1. Пора порция ---
    if check.get("ok") and not st.get("mailing_paused"):
        fp = f"need:{queued}:{left}:{st.get('club_cohort') or '-'}"
        if not _should_skip(st, kind=ALERT_NEED_PORTION, fingerprint=fp, now=now):
            text = (
                "📬 <b>Подарочная волна — пора порция Т1</b>\n\n"
                f"В очереди на отбор (бот): <b>{queued}</b>\n"
                f"Осталось мест этапа 1: <b>{left}</b> "
                f"(порог «хватит» = {2 * left})\n"
                f"Последняя когорта: <code>{st.get('club_cohort') or '—'}</code>\n\n"
                "Заявок мало — можно звать следующую порцию.\n"
                "<code>/gift_campaign portion</code> "
                "или <code>/gift_campaign portion K1</code> / K2 / K3"
            )
            if await _send(bot, text):
                await _save_alert(
                    user_storage, state=st, kind=ALERT_NEED_PORTION, fingerprint=fp
                )
                st = await user_storage.get_or_create_gift_campaign_state()
                sent[ALERT_NEED_PORTION] = True

    # --- 2. Заявок хватает — порцию не слать ---
    if check.get("reason") == "enough_apps":
        fp = f"enough:{queued}:{left}"
        if not _should_skip(st, kind=ALERT_ENOUGH_APPS, fingerprint=fp, now=now):
            text = (
                "✅ <b>Подарочная волна — заявок хватает</b>\n\n"
                f"Ждут отбора: <b>{queued}</b>\n"
                f"Осталось мест этапа 1: <b>{left}</b>\n"
                f"Правило: очередь ≥ 2 × места → "
                f"<b>{queued} ≥ {2 * left}</b>\n\n"
                "Новую порцию Т1 слать не нужно. Смотрите отбор волны.\n"
                "<code>/gift_campaign</code>"
            )
            if await _send(bot, text):
                await _save_alert(
                    user_storage, state=st, kind=ALERT_ENOUGH_APPS, fingerprint=fp
                )
                st = await user_storage.get_or_create_gift_campaign_state()
                sent[ALERT_ENOUGH_APPS] = True

    # --- 3. Пора проводить отбор (wave) ---
    if not st.get("waves_paused"):
        next_idx, wave_row = await _next_wave_index(user_storage)
        hours = await _hours_since_campaign_start(user_storage)
        status = (wave_row or {}).get("status")
        # wave уже running — не зовём «пора отбор», это про старт
        can_start = next_idx > 0 and status not in ("running", "paused", "done")
        # ориентир ТЗ: волна 1 ~день 3; дальше — когда очередь набралась
        time_ok = (hours is not None and hours >= 48) or queued >= WAVE_QUEUE_MIN
        queue_ok = queued >= max(15, WAVE_QUEUE_MIN // 2) or check.get(
            "reason"
        ) == "enough_apps"
        if can_start and time_ok and queue_ok:
            fp = f"wave:{next_idx}:{queued}:{left}"
            if not _should_skip(st, kind=ALERT_READY_WAVE, fingerprint=fp, now=now):
                text = (
                    "🎯 <b>Подарочная волна — пора отбор</b>\n\n"
                    f"Следующая волна: <b>{next_idx}</b> из 6\n"
                    f"В очереди (бот): <b>{queued}</b> "
                    f"(на волну нужно до {WAVE_QUEUE_MIN}: "
                    f"{DRAW_SLOTS} жребий + {SCORE_SLOTS} баллы)\n"
                    f"Часов с старта кампании: "
                    f"<b>{int(hours) if hours is not None else '—'}</b>\n\n"
                    f"Команда: <code>/gift_campaign wave {next_idx}</code>\n"
                    "Статус: <code>/gift_campaign</code>"
                )
                if await _send(bot, text):
                    await _save_alert(
                        user_storage,
                        state=st,
                        kind=ALERT_READY_WAVE,
                        fingerprint=fp,
                    )
                    st = await user_storage.get_or_create_gift_campaign_state()
                    sent[ALERT_READY_WAVE] = True

    # --- 4. Блокировки в последней рассылке ---
    br = await _latest_gift_mailing_block_rate(user_storage)
    if br and br["rate"] > BLOCK_RATE_LIMIT:
        fp = f"blocks:{br['campaign_id']}:{br['blocked']}:{br['sent']}"
        if not _should_skip(st, kind=ALERT_HIGH_BLOCKS, fingerprint=fp, now=now):
            pct = br["rate"] * 100
            text = (
                "⛔️ <b>Подарочная волна — много блокировок</b>\n\n"
                f"Рассылка: <code>{br['name']}</code> "
                f"(#{br['campaign_id']})\n"
                f"Доставлено: <b>{br['sent']}</b>, "
                f"заблокировали: <b>{br['blocked']}</b> "
                f"(<b>{pct:.1f}%</b>, порог {BLOCK_RATE_LIMIT * 100:.0f}%)\n\n"
                "По ТЗ — пауза рассылки и разбор текста.\n"
                "<code>/gift_campaign pause</code>"
            )
            if await _send(bot, text):
                await _save_alert(
                    user_storage, state=st, kind=ALERT_HIGH_BLOCKS, fingerprint=fp
                )
                sent[ALERT_HIGH_BLOCKS] = True
                # автопауза mailing при высоком block rate
                try:
                    await user_storage.update_gift_campaign_state(mailing_paused=True)
                except Exception:
                    pass

    return {"ok": True, "sent": sent, "check": check}
