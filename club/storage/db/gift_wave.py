"""Хранение подарочных волн и встречающих."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")


class GiftWaveMixin:
    """gift_wave / gift_wave_member / club_greeter*."""

    # ----- waves -----

    async def create_gift_wave(
        self,
        *,
        title: str,
        batch_size: int = 25,
        interval_hours: int = 48,
        gift_days: int = 30,
    ) -> Optional[int]:
        batch_size = max(1, min(25, int(batch_size)))
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO gift_wave
                        (title, batch_size, interval_hours, gift_days, status)
                    VALUES ($1, $2, $3, $4, 'draft')
                    RETURNING id
                    """,
                    title.strip()[:200],
                    batch_size,
                    max(1, int(interval_hours)),
                    max(1, int(gift_days)),
                )
                return int(row["id"]) if row else None
        except Exception as e:
            logger.error("create_gift_wave: %s", e, exc_info=True)
            return None

    async def get_gift_wave(self, wave_id: int) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM gift_wave WHERE id = $1", wave_id
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_gift_wave: %s", e)
            return None

    async def list_gift_waves(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM gift_wave
                    ORDER BY id DESC
                    LIMIT $1
                    """,
                    limit,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_gift_waves: %s", e)
            return []

    async def set_gift_wave_status(
        self,
        wave_id: int,
        status: str,
        *,
        paused_reason: Optional[str] = None,
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE gift_wave
                    SET status = $2,
                        paused_reason = $3,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    wave_id,
                    status,
                    paused_reason,
                )
                return True
        except Exception as e:
            logger.error("set_gift_wave_status: %s", e)
            return False

    async def update_gift_wave_timing(
        self,
        wave_id: int,
        *,
        batch_size: Optional[int] = None,
        interval_hours: Optional[int] = None,
    ) -> bool:
        wave = await self.get_gift_wave(wave_id)
        if not wave:
            return False
        bs = wave["batch_size"] if batch_size is None else max(1, min(25, batch_size))
        ih = (
            wave["interval_hours"]
            if interval_hours is None
            else max(1, int(interval_hours))
        )
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE gift_wave
                    SET batch_size = $2, interval_hours = $3, updated_at = NOW()
                    WHERE id = $1
                    """,
                    wave_id,
                    bs,
                    ih,
                )
                return True
        except Exception as e:
            logger.error("update_gift_wave_timing: %s", e)
            return False

    async def enqueue_gift_wave_members(
        self, wave_id: int, user_ids: Sequence[int]
    ) -> int:
        if not user_ids:
            return 0
        added = 0
        try:
            async with self.get_connection() as conn:
                for uid in user_ids:
                    uid = int(uid)
                    if uid <= 0:
                        continue
                    r = await conn.execute(
                        """
                        INSERT INTO gift_wave_member (wave_id, user_id, status)
                        VALUES ($1, $2, 'queued')
                        ON CONFLICT (wave_id, user_id) DO NOTHING
                        """,
                        wave_id,
                        uid,
                    )
                    if r and r.endswith("1"):
                        added += 1
            return added
        except Exception as e:
            logger.error("enqueue_gift_wave_members: %s", e, exc_info=True)
            return added

    async def list_queued_wave_members(
        self, wave_id: int, *, limit: int
    ) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM gift_wave_member
                    WHERE wave_id = $1 AND status = 'queued'
                    ORDER BY queued_at ASC, user_id ASC
                    LIMIT $2
                    """,
                    wave_id,
                    limit,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_queued_wave_members: %s", e)
            return []

    async def mark_wave_member_granted(
        self, wave_id: int, user_id: int
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE gift_wave_member
                    SET status = 'granted', granted_at = NOW()
                    WHERE wave_id = $1 AND user_id = $2 AND status = 'queued'
                    """,
                    wave_id,
                    user_id,
                )
                return True
        except Exception as e:
            logger.error("mark_wave_member_granted: %s", e)
            return False

    async def mark_wave_member_declined(
        self, wave_id: int, user_id: int, *, reason: str = ""
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE gift_wave_member
                    SET status = 'declined'
                    WHERE wave_id = $1 AND user_id = $2 AND status = 'queued'
                    """,
                    wave_id,
                    user_id,
                )
                return True
        except Exception as e:
            logger.error(
                "mark_wave_member_declined wave=%s uid=%s reason=%s: %s",
                wave_id,
                user_id,
                reason,
                e,
            )
            return False

    async def release_granted_gift_ticket(
        self, user_id: int, *, reason: str = "bot_blocked"
    ) -> Optional[Dict[str, Any]]:
        """Освободить неактивированный билет (granted → declined)."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE gift_wave_member
                    SET status = 'declined'
                    WHERE user_id = $1 AND status = 'granted'
                    RETURNING *
                    """,
                    user_id,
                )
                if row:
                    logger.info(
                        "release_granted_gift_ticket uid=%s wave=%s reason=%s",
                        user_id,
                        row["wave_id"],
                        reason,
                    )
                    return dict(row)
                return None
        except Exception as e:
            logger.error(
                "release_granted_gift_ticket uid=%s reason=%s: %s",
                user_id,
                reason,
                e,
                exc_info=True,
            )
            return None

    async def activate_pending_gift_ticket(
        self, user_id: int, *, joined_at: Optional[datetime] = None
    ) -> Optional[Dict[str, Any]]:
        """Билет granted → activated при входе (лицензию выдаёт вызывающий)."""
        joined_at = joined_at or datetime.now(MSK)
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE gift_wave_member gwm
                    SET status = 'activated',
                        activated_at = COALESCE(activated_at, $2),
                        joined_at = COALESCE(joined_at, $2)
                    FROM gift_wave gw
                    WHERE gwm.wave_id = gw.id
                      AND gwm.user_id = $1
                      AND gwm.status = 'granted'
                    RETURNING gwm.*, gw.gift_days, gw.campaign
                    """,
                    user_id,
                    joined_at,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("activate_pending_gift_ticket: %s", e, exc_info=True)
            return None

    async def expire_unactivated_gift_tickets(self, *, days: int = 7) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    UPDATE gift_wave_member
                    SET status = 'expired', expired_at = NOW()
                    WHERE status = 'granted'
                      AND granted_at IS NOT NULL
                      AND granted_at <= NOW() - ($1 || ' days')::interval
                    RETURNING *
                    """,
                    str(days),
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("expire_unactivated_gift_tickets: %s", e, exc_info=True)
            return []

    async def get_pending_gift_ticket(self, user_id: int) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT gwm.*, gw.gift_days, gw.campaign, gw.title
                    FROM gift_wave_member gwm
                    JOIN gift_wave gw ON gw.id = gwm.wave_id
                    WHERE gwm.user_id = $1 AND gwm.status = 'granted'
                    ORDER BY gwm.granted_at DESC NULLS LAST
                    LIMIT 1
                    """,
                    user_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_pending_gift_ticket: %s", e)
            return None

    async def touch_wave_last_batch(self, wave_id: int) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE gift_wave
                    SET last_batch_at = NOW(), updated_at = NOW()
                    WHERE id = $1
                    """,
                    wave_id,
                )
        except Exception as e:
            logger.error("touch_wave_last_batch: %s", e)

    async def mark_wave_member_joined(
        self, user_id: int, *, joined_at: Optional[datetime] = None
    ) -> List[int]:
        """Помечает activated во всех волнах, где был granted. Возвращает wave_ids."""
        joined_at = joined_at or datetime.now(MSK)
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    UPDATE gift_wave_member
                    SET status = 'activated',
                        activated_at = COALESCE(activated_at, $2),
                        joined_at = COALESCE(joined_at, $2)
                    WHERE user_id = $1
                      AND status = 'granted'
                    RETURNING wave_id
                    """,
                    user_id,
                    joined_at,
                )
                return [int(r["wave_id"]) for r in rows]
        except Exception as e:
            logger.error("mark_wave_member_joined: %s", e)
            return []

    async def mark_wave_member_spoke(
        self,
        user_id: int,
        *,
        first_msg_at: datetime,
        first_msg_id: int,
    ) -> Optional[int]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE gift_wave_member
                    SET status = 'spoke',
                        first_msg_at = COALESCE(first_msg_at, $2),
                        first_msg_id = COALESCE(first_msg_id, $3)
                    WHERE user_id = $1
                      AND status IN ('joined', 'granted', 'activated')
                      AND first_msg_at IS NULL
                    RETURNING wave_id
                    """,
                    user_id,
                    first_msg_at,
                    first_msg_id,
                )
                return int(row["wave_id"]) if row else None
        except Exception as e:
            logger.error("mark_wave_member_spoke: %s", e)
            return None

    async def wave_counts(self, wave_id: int) -> Dict[str, int]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT status, COUNT(*)::int AS n
                    FROM gift_wave_member
                    WHERE wave_id = $1
                    GROUP BY status
                    """,
                    wave_id,
                )
                out = {r["status"]: int(r["n"]) for r in rows}
                out["total"] = sum(out.values())
                return out
        except Exception as e:
            logger.error("wave_counts: %s", e)
            return {"total": 0}

    async def list_running_gift_waves(self) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM gift_wave
                    WHERE status = 'running'
                    ORDER BY id ASC
                    """
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_running_gift_waves: %s", e)
            return []

    # ----- greeters -----

    async def upsert_club_greeter(
        self,
        user_id: int,
        *,
        active: bool = True,
        capacity: int = 3,
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO club_greeter (user_id, active, capacity)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id) DO UPDATE
                    SET active = EXCLUDED.active,
                        capacity = EXCLUDED.capacity,
                        paused_until = NULL,
                        miss_streak = 0
                    """,
                    user_id,
                    active,
                    max(1, min(20, capacity)),
                )
                return True
        except Exception as e:
            logger.error("upsert_club_greeter: %s", e)
            return False

    async def set_greeter_active(self, user_id: int, active: bool) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE club_greeter
                    SET active = $2,
                        paused_until = CASE WHEN $2 THEN NULL ELSE paused_until END
                    WHERE user_id = $1
                    """,
                    user_id,
                    active,
                )
                return True
        except Exception as e:
            logger.error("set_greeter_active: %s", e)
            return False

    async def deactivate_greeters_without_active_license(self) -> List[int]:
        """Снять с пула тех, у кого нет действующей лицензии."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    UPDATE club_greeter g
                    SET active = FALSE,
                        notes = CASE
                            WHEN g.notes IS NULL OR btrim(g.notes) = ''
                            THEN 'auto: no active license'
                            WHEN g.notes LIKE '%auto: no active license%'
                            THEN g.notes
                            ELSE g.notes || '; auto: no active license'
                        END
                    WHERE g.active = TRUE
                      AND NOT EXISTS (
                          SELECT 1 FROM license l
                          WHERE l.user_id = g.user_id
                            AND l.status = 'active'
                            AND l.expires_at > NOW()
                      )
                    RETURNING g.user_id
                    """
                )
                return [int(r["user_id"]) for r in rows]
        except Exception as e:
            logger.error("deactivate_greeters_without_active_license: %s", e)
            return []

    async def pause_greeter_until(self, user_id: int, until: datetime) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE club_greeter
                    SET paused_until = $2
                    WHERE user_id = $1
                    """,
                    user_id,
                    until,
                )
                return True
        except Exception as e:
            logger.error("pause_greeter_until: %s", e)
            return False

    async def list_club_greeters(self) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT g.*, u.first_name, u.username
                    FROM club_greeter g
                    LEFT JOIN users u ON u.user_id = g.user_id
                    ORDER BY g.active DESC, g.added_at ASC
                    """
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_club_greeters: %s", e)
            return []

    async def greeter_assignments_today(self, greeter_id: int) -> int:
        today = datetime.now(MSK).date()
        start = datetime(today.year, today.month, today.day, tzinfo=MSK)
        try:
            async with self.get_connection() as conn:
                n = await conn.fetchval(
                    """
                    SELECT COUNT(*)::int
                    FROM club_greeter_assignment
                    WHERE greeter_id = $1
                      AND assigned_at >= $2
                      AND status IN ('pending', 'replied', 'bot_followup')
                    """,
                    greeter_id,
                    start,
                )
                return int(n or 0)
        except Exception as e:
            logger.error("greeter_assignments_today: %s", e)
            return 999

    async def pick_club_greeter(
        self, *, exclude_ids: Optional[Sequence[int]] = None
    ) -> Optional[int]:
        exclude = {int(x) for x in (exclude_ids or [])}
        now = datetime.now(MSK)
        today_start = datetime(
            now.year, now.month, now.day, tzinfo=MSK
        )
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT g.user_id, g.capacity,
                           COALESCE(a.cnt, 0)::int AS today_cnt
                    FROM club_greeter g
                    LEFT JOIN LATERAL (
                        SELECT COUNT(*) AS cnt
                        FROM club_greeter_assignment ga
                        WHERE ga.greeter_id = g.user_id
                          AND ga.assigned_at >= $1
                          AND ga.status IN ('pending', 'replied', 'bot_followup')
                    ) a ON TRUE
                    WHERE g.active = TRUE
                      AND (g.paused_until IS NULL OR g.paused_until <= $2)
                      AND EXISTS (
                          SELECT 1 FROM license l
                          WHERE l.user_id = g.user_id
                            AND l.status = 'active'
                            AND l.expires_at > NOW()
                      )
                    ORDER BY COALESCE(a.cnt, 0) ASC, g.added_at ASC
                    """,
                    today_start,
                    now,
                )
                for r in rows:
                    uid = int(r["user_id"])
                    if uid in exclude:
                        continue
                    # Равномерно: без суточного cap — всегда берём наименее загруженного.
                    return uid
                return None
        except Exception as e:
            logger.error("pick_club_greeter: %s", e, exc_info=True)
            return None

    async def create_greeter_assignment(
        self,
        *,
        greeter_id: int,
        newcomer_id: int,
        newcomer_msg_id: Optional[int],
        attempt: int = 1,
    ) -> Optional[int]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO club_greeter_assignment
                        (greeter_id, newcomer_id, newcomer_msg_id, attempt, status)
                    VALUES ($1, $2, $3, $4, 'pending')
                    RETURNING id
                    """,
                    greeter_id,
                    newcomer_id,
                    newcomer_msg_id,
                    attempt,
                )
                return int(row["id"]) if row else None
        except Exception as e:
            logger.error("create_greeter_assignment: %s", e)
            return None

    async def list_pending_greeter_assignments(
        self, *, older_than_minutes: int
    ) -> List[Dict[str, Any]]:
        cutoff = datetime.now(MSK) - timedelta(minutes=older_than_minutes)
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT *
                    FROM club_greeter_assignment
                    WHERE status = 'pending'
                      AND assigned_at <= $1
                    ORDER BY assigned_at ASC
                    """,
                    cutoff,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_pending_greeter_assignments: %s", e)
            return []

    async def mark_greeter_replied(
        self, assignment_id: int, *, replied_at: Optional[datetime] = None
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE club_greeter_assignment
                    SET status = 'replied',
                        replied_at = COALESCE($2, NOW())
                    WHERE id = $1 AND status = 'pending'
                    """,
                    assignment_id,
                    replied_at,
                )
                await conn.execute(
                    """
                    UPDATE club_greeter g
                    SET miss_streak = 0
                    FROM club_greeter_assignment a
                    WHERE a.id = $1 AND g.user_id = a.greeter_id
                    """,
                    assignment_id,
                )
                return True
        except Exception as e:
            logger.error("mark_greeter_replied: %s", e)
            return False

    async def mark_greeter_reassigned(self, assignment_id: int) -> Optional[int]:
        """Помечает reassigned; если miss_streak≥5 — снимает с пула.
        Возвращает greeter_id при авто-деактивации, иначе None.
        """
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE club_greeter_assignment
                    SET status = 'reassigned', reassigned_at = NOW()
                    WHERE id = $1 AND status = 'pending'
                    """,
                    assignment_id,
                )
                row = await conn.fetchrow(
                    """
                    UPDATE club_greeter g
                    SET miss_streak = g.miss_streak + 1
                    FROM club_greeter_assignment a
                    WHERE a.id = $1 AND g.user_id = a.greeter_id
                    RETURNING g.user_id, g.miss_streak
                    """,
                    assignment_id,
                )
                if row and int(row["miss_streak"]) >= 5:
                    await conn.execute(
                        """
                        UPDATE club_greeter
                        SET active = FALSE
                        WHERE user_id = $1
                        """,
                        int(row["user_id"]),
                    )
                    return int(row["user_id"])
                return None
        except Exception as e:
            logger.error("mark_greeter_reassigned: %s", e)
            return None

    async def mark_greeter_bot_followup(self, assignment_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE club_greeter_assignment
                    SET status = 'bot_followup'
                    WHERE id = $1 AND status = 'pending'
                    """,
                    assignment_id,
                )
                return True
        except Exception as e:
            logger.error("mark_greeter_bot_followup: %s", e)
            return False

    async def find_pending_assignment_for_reply(
        self, *, greeter_id: int, newcomer_id: int
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT *
                    FROM club_greeter_assignment
                    WHERE greeter_id = $1
                      AND newcomer_id = $2
                      AND status = 'pending'
                    ORDER BY assigned_at DESC
                    LIMIT 1
                    """,
                    greeter_id,
                    newcomer_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("find_pending_assignment_for_reply: %s", e)
            return None

    async def latest_open_assignment_for_newcomer(
        self, newcomer_id: int
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT *
                    FROM club_greeter_assignment
                    WHERE newcomer_id = $1
                      AND status = 'pending'
                    ORDER BY assigned_at DESC
                    LIMIT 1
                    """,
                    newcomer_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("latest_open_assignment_for_newcomer: %s", e)
            return None

    async def count_user_club_group_messages(
        self, user_id: int, *, before_message_id: Optional[int] = None
    ) -> int:
        """Сколько сообщений пользователя в клубной группе (до указанного mid)."""
        from config import config

        chat_id = int(config.CLUB_GROUP_ID or 0)
        if not chat_id:
            return 0
        try:
            async with self.get_connection() as conn:
                if before_message_id:
                    n = await conn.fetchval(
                        """
                        SELECT COUNT(*)::int
                        FROM messages
                        WHERE chat_id = $1
                          AND user_id = $2
                          AND sender_type = 'user'
                          AND telegram_message_id IS NOT NULL
                          AND telegram_message_id < $3
                        """,
                        chat_id,
                        user_id,
                        int(before_message_id),
                    )
                else:
                    n = await conn.fetchval(
                        """
                        SELECT COUNT(*)::int
                        FROM messages
                        WHERE chat_id = $1
                          AND user_id = $2
                          AND sender_type = 'user'
                          AND telegram_message_id IS NOT NULL
                        """,
                        chat_id,
                        user_id,
                    )
                return int(n or 0)
        except Exception as e:
            logger.error("count_user_club_group_messages: %s", e)
            return 999

    async def newcomer_had_greeter_before(self, newcomer_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                n = await conn.fetchval(
                    """
                    SELECT COUNT(*)::int
                    FROM club_greeter_assignment
                    WHERE newcomer_id = $1
                    """,
                    newcomer_id,
                )
                return bool(n and int(n) > 0)
        except Exception as e:
            logger.error("newcomer_had_greeter_before: %s", e)
            return True
