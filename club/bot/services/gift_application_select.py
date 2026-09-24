"""Отбор в волны: жребий + баллы (АНК-5)."""

from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Any, Dict, List, Optional, Sequence, Set

from storage.db.gift_application import CAMPAIGN_ID

logger = logging.getLogger(__name__)

# волна_index -> источники для score-мест (после общего жребия)
WAVE_SCORE_SOURCES: Dict[int, Sequence[str]] = {
    1: ("bot", "other"),
    2: ("bot", "other"),
    3: ("bib",),
    4: ("bib",),
    5: ("tg", "ig", "yt"),
    6: ("tg", "ig", "yt"),
}

DRAW_SLOTS = 5
SCORE_SLOTS = 20


async def _admin_user_ids(user_storage) -> Set[int]:
    """Telegram id админов + SUPER_ADMIN — их не берём в розыгрыш."""
    out: Set[int] = set()
    try:
        from config import config

        sid = int(getattr(config, "SUPER_ADMIN_ID", 0) or 0)
        if sid:
            out.add(sid)
    except Exception:
        pass
    try:
        rows = await user_storage.list_telegram_admin_ids()
        for r in rows or []:
            uid = r.get("telegram_user_id") if isinstance(r, dict) else None
            if uid is not None:
                out.add(int(uid))
    except Exception as e:
        logger.warning("list admins for wave select: %s", e)
    return out


def _draw_key(seed: str, user_id: int) -> str:
    return hashlib.sha256(f"{seed}:{user_id}".encode("utf-8")).hexdigest()


async def ensure_campaign_wave(
    user_storage,
    *,
    wave_index: int,
    campaign: str = CAMPAIGN_ID,
    gift_days: int = 30,
) -> Optional[Dict[str, Any]]:
    async with user_storage.get_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM gift_wave
            WHERE campaign = $1 AND wave_index = $2
            ORDER BY id DESC
            LIMIT 1
            """,
            campaign,
            wave_index,
        )
        if row:
            return dict(row)
        seed = secrets.token_hex(16)
        stage = 1 if wave_index <= 2 else (2 if wave_index <= 4 else 3)
        title = f"gift-2026-09 волна {wave_index}"
        new = await conn.fetchrow(
            """
            INSERT INTO gift_wave
                (title, batch_size, interval_hours, gift_days, status,
                 draw_seed, campaign, stage, wave_index)
            VALUES ($1, $2, 48, $3, 'draft', $4, $5, $6, $7)
            RETURNING *
            """,
            title,
            DRAW_SLOTS + SCORE_SLOTS,
            gift_days,
            seed,
            campaign,
            str(stage),
            wave_index,
        )
        return dict(new) if new else None


async def select_applications_for_wave(
    user_storage,
    *,
    wave_id: int,
    wave_index: int,
    campaign: str = CAMPAIGN_ID,
) -> Dict[str, Any]:
    wave = await user_storage.get_gift_wave(wave_id)
    if not wave:
        return {"ok": False, "reason": "no_wave"}

    seed = (wave.get("draw_seed") or "").strip()
    if not seed:
        seed = secrets.token_hex(16)
        async with user_storage.get_connection() as conn:
            await conn.execute(
                "UPDATE gift_wave SET draw_seed = $2, updated_at = NOW() WHERE id = $1",
                wave_id,
                seed,
            )

    queued = await user_storage.list_gift_applications_queued(campaign=campaign)
    admin_ids = await _admin_user_ids(user_storage)
    skipped_admins = 0
    if admin_ids:
        before = len(queued)
        queued = [a for a in queued if int(a["user_id"]) not in admin_ids]
        skipped_admins = before - len(queued)
        if skipped_admins:
            logger.info(
                "wave select skip admins n=%s",
                skipped_admins,
            )

    from bot.services.gift_application_eligibility import user_ids_already_in_club

    skipped_in_club = 0
    in_club_ids = await user_ids_already_in_club(
        user_storage, [int(a["user_id"]) for a in queued]
    )
    if in_club_ids:
        kept: List[Dict[str, Any]] = []
        drop_app_ids: List[int] = []
        for a in queued:
            if int(a["user_id"]) in in_club_ids:
                skipped_in_club += 1
                drop_app_ids.append(int(a["id"]))
            else:
                kept.append(a)
        queued = kept
        if drop_app_ids:
            async with user_storage.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE gift_application
                    SET status = 'ineligible',
                        eligible = FALSE,
                        ineligible_reason = 'already_in_club',
                        updated_at = NOW()
                    WHERE id = ANY($1::bigint[])
                      AND status = 'queued'
                    """,
                    drop_app_ids,
                )
            logger.info(
                "wave select skip already in club n=%s",
                skipped_in_club,
            )

    if not queued:
        return {
            "ok": True,
            "drawn": 0,
            "scored": 0,
            "selected_ids": [],
            "skipped_admins": skipped_admins,
            "skipped_in_club": skipped_in_club,
        }

    # 1) жребий среди всех (без админов)
    by_draw = sorted(
        queued,
        key=lambda a: (_draw_key(seed, int(a["user_id"])), int(a["id"])),
    )
    drawn = by_draw[:DRAW_SLOTS]
    drawn_ids: Set[int] = {int(a["id"]) for a in drawn}

    # 2) баллы из источников этапа
    sources = set(WAVE_SCORE_SOURCES.get(wave_index, ("bot", "other")))
    remaining = [a for a in queued if int(a["id"]) not in drawn_ids]

    def _score_key(a: Dict[str, Any]):
        return (
            -(int(a.get("score") or 0)),
            a.get("submitted_at") or a.get("started_at"),
            int(a["id"]),
        )

    stage_pool = [a for a in remaining if (a.get("source") or "other") in sources]
    stage_pool.sort(key=_score_key)
    scored: List[Dict[str, Any]] = stage_pool[:SCORE_SLOTS]

    if len(scored) < SCORE_SLOTS:
        need = SCORE_SLOTS - len(scored)
        scored_ids = {int(a["id"]) for a in scored} | drawn_ids
        leftovers = [a for a in remaining if int(a["id"]) not in scored_ids]
        leftovers.sort(key=_score_key)
        scored.extend(leftovers[:need])

    selected: List[Dict[str, Any]] = []
    for a in drawn:
        selected.append({**a, "_selection": "draw", "_status": "drawn"})
    for a in scored:
        selected.append({**a, "_selection": "score", "_status": "selected"})

    async with user_storage.get_connection() as conn:
        for a in selected:
            uid = int(a["user_id"])
            app_id = int(a["id"])
            selection = a["_selection"]
            st = a["_status"]
            await conn.execute(
                """
                INSERT INTO gift_wave_member
                    (wave_id, user_id, status, application_id, selection, source, score)
                VALUES ($1, $2, 'queued', $3, $4, $5, $6)
                ON CONFLICT (wave_id, user_id) DO UPDATE SET
                    application_id = EXCLUDED.application_id,
                    selection = EXCLUDED.selection,
                    source = EXCLUDED.source,
                    score = EXCLUDED.score
                """,
                wave_id,
                uid,
                app_id,
                selection,
                a.get("source"),
                a.get("score"),
            )
            await conn.execute(
                """
                UPDATE gift_application
                SET status = $2, wave_id = $3, updated_at = NOW()
                WHERE id = $1
                """,
                app_id,
                st,
                wave_id,
            )
            try:
                await user_storage.log_interaction(
                    user_id=uid,
                    event_category="gift_application",
                    event_type="gift_app_selected",
                    data={
                        "application_id": app_id,
                        "wave_id": wave_id,
                        "selection": selection,
                        "score": a.get("score"),
                    },
                    source="gift_application",
                    outcome="success",
                )
            except Exception:
                pass

    return {
        "ok": True,
        "drawn": len(drawn),
        "scored": len(scored),
        "selected_ids": [int(a["id"]) for a in selected],
        "wave_id": wave_id,
        "skipped_admins": skipped_admins,
        "skipped_in_club": skipped_in_club,
    }


async def finish_campaign_not_selected(
    user_storage,
    *,
    campaign: str = CAMPAIGN_ID,
) -> int:
    return await user_storage.mark_gift_applications_not_selected(campaign=campaign)
