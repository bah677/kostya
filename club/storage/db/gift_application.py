"""CRUD анкеты подарочной волны (gift-2026-09)."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CAMPAIGN_ID = "gift-2026-09"


class GiftApplicationMixin:
    async def get_gift_application(
        self, user_id: int, *, campaign: str = CAMPAIGN_ID
    ) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT *
                FROM gift_application
                WHERE campaign = $1 AND user_id = $2
                """,
                campaign,
                user_id,
            )
            return dict(row) if row else None

    async def get_gift_application_by_id(self, application_id: int) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM gift_application WHERE id = $1",
                application_id,
            )
            return dict(row) if row else None

    async def upsert_gift_application_start(
        self,
        user_id: int,
        *,
        source: str = "other",
        campaign: str = CAMPAIGN_ID,
        eligible: bool = True,
        ineligible_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        status = "ineligible" if not eligible else "draft"
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO gift_application
                    (campaign, user_id, source, eligible, ineligible_reason, status, started_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW())
                ON CONFLICT (campaign, user_id) DO UPDATE SET
                    source = CASE
                        WHEN gift_application.status IN ('draft', 'ineligible')
                        THEN EXCLUDED.source
                        ELSE gift_application.source
                    END,
                    eligible = CASE
                        WHEN gift_application.status IN ('draft', 'ineligible')
                        THEN EXCLUDED.eligible
                        ELSE gift_application.eligible
                    END,
                    ineligible_reason = CASE
                        WHEN gift_application.status IN ('draft', 'ineligible')
                        THEN EXCLUDED.ineligible_reason
                        ELSE gift_application.ineligible_reason
                    END,
                    status = CASE
                        WHEN gift_application.status IN ('draft', 'ineligible')
                             AND EXCLUDED.status = 'ineligible'
                        THEN 'ineligible'
                        WHEN gift_application.status = 'ineligible'
                             AND EXCLUDED.eligible
                        THEN 'draft'
                        ELSE gift_application.status
                    END,
                    updated_at = NOW()
                RETURNING *
                """,
                campaign,
                user_id,
                source,
                eligible,
                ineligible_reason,
                status,
            )
            return dict(row)

    async def update_gift_application_answers(
        self,
        application_id: int,
        *,
        q1_about: Optional[str] = None,
        q2_why: Optional[str] = None,
        q3_ready: Optional[bool] = None,
        rules_accepted: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        sets: List[str] = ["updated_at = NOW()"]
        args: List[Any] = [application_id]
        idx = 2
        if q1_about is not None:
            sets.append(f"q1_about = ${idx}")
            args.append(q1_about)
            idx += 1
        if q2_why is not None:
            sets.append(f"q2_why = ${idx}")
            args.append(q2_why)
            idx += 1
        if q3_ready is not None:
            sets.append(f"q3_ready = ${idx}")
            args.append(q3_ready)
            idx += 1
        if rules_accepted is not None:
            sets.append(f"rules_accepted = ${idx}")
            args.append(rules_accepted)
            idx += 1
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE gift_application
                SET {', '.join(sets)}
                WHERE id = $1
                RETURNING *
                """,
                *args,
            )
            return dict(row) if row else None

    async def submit_gift_application(self, application_id: int) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                """
                UPDATE gift_application
                SET status = 'submitted',
                    submitted_at = NOW(),
                    updated_at = NOW()
                WHERE id = $1
                  AND status = 'draft'
                  AND eligible
                  AND q1_about IS NOT NULL
                  AND q2_why IS NOT NULL
                  AND q3_ready IS NOT NULL
                  AND rules_accepted
                RETURNING *
                """,
                application_id,
            )
            return dict(row) if row else None

    async def list_stuck_gift_submits(
        self, *, campaign: str = CAMPAIGN_ID, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Черновики, где человек дошёл до правил, но submit не прошёл (баг q3_ready=False)."""
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM gift_application
                WHERE campaign = $1
                  AND status = 'draft'
                  AND eligible
                  AND q1_about IS NOT NULL
                  AND q2_why IS NOT NULL
                  AND q3_ready IS NOT NULL
                  AND rules_accepted
                ORDER BY updated_at
                LIMIT $2
                """,
                campaign,
                limit,
            )
            return [dict(r) for r in rows]

    async def set_gift_application_verdict(
        self,
        application_id: int,
        *,
        verdict: str,
        reason: Optional[str] = None,
        score: Optional[int] = None,
        score_parts: Optional[Dict[str, Any]] = None,
        reviewed_by: Optional[int] = None,
        status: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if status is None:
            if verdict == "pass":
                status = "queued"
            elif verdict == "reject":
                status = "rejected"
            elif verdict == "care":
                status = "care"
            else:
                status = "submitted"
        parts_json = json.dumps(score_parts, ensure_ascii=False) if score_parts is not None else None
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                """
                UPDATE gift_application
                SET verdict = $2,
                    verdict_reason = $3,
                    score = COALESCE($4, score),
                    score_parts = COALESCE($5::jsonb, score_parts),
                    status = $6,
                    reviewed_by = COALESCE($7, reviewed_by),
                    reviewed_at = CASE WHEN $7 IS NOT NULL THEN NOW() ELSE reviewed_at END,
                    updated_at = NOW()
                WHERE id = $1
                RETURNING *
                """,
                application_id,
                verdict,
                reason,
                score,
                parts_json,
                status,
                reviewed_by,
            )
            return dict(row) if row else None

    async def list_gift_applications_for_review(
        self, *, campaign: str = CAMPAIGN_ID, limit: int = 20
    ) -> List[Dict[str, Any]]:
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM gift_application
                WHERE campaign = $1
                  AND (verdict = 'review' OR (status = 'submitted' AND verdict IS NULL))
                ORDER BY submitted_at NULLS LAST, id
                LIMIT $2
                """,
                campaign,
                limit,
            )
            return [dict(r) for r in rows]

    async def list_gift_applications_queued(
        self, *, campaign: str = CAMPAIGN_ID
    ) -> List[Dict[str, Any]]:
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM gift_application
                WHERE campaign = $1
                  AND status = 'queued'
                  AND verdict = 'pass'
                ORDER BY score DESC NULLS LAST, submitted_at ASC NULLS LAST, id
                """,
                campaign,
            )
            return [dict(r) for r in rows]

    async def mark_gift_application_selected(
        self,
        application_id: int,
        *,
        wave_id: int,
        selection: str,
        status: str = "selected",
    ) -> None:
        async with self.get_connection() as conn:
            await conn.execute(
                """
                UPDATE gift_application
                SET status = $2,
                    wave_id = $3,
                    updated_at = NOW()
                WHERE id = $1
                """,
                application_id,
                status,
                wave_id,
            )

    async def mark_gift_applications_not_selected(
        self, *, campaign: str = CAMPAIGN_ID
    ) -> int:
        async with self.get_connection() as conn:
            result = await conn.execute(
                """
                UPDATE gift_application
                SET status = 'not_selected', updated_at = NOW()
                WHERE campaign = $1
                  AND status = 'queued'
                  AND verdict = 'pass'
                """,
                campaign,
            )
            try:
                return int(result.split()[-1])
            except Exception:
                return 0

    async def gift_application_stats(
        self, *, campaign: str = CAMPAIGN_ID
    ) -> Dict[str, int]:
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT status, COUNT(*)::int AS cnt
                FROM gift_application
                WHERE campaign = $1
                GROUP BY status
                """,
                campaign,
            )
            return {r["status"]: r["cnt"] for r in rows}

    async def count_gift_applications_by_source(
        self, *, campaign: str = CAMPAIGN_ID
    ) -> Dict[str, int]:
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT source, COUNT(*)::int AS cnt
                FROM gift_application
                WHERE campaign = $1
                GROUP BY source
                """,
                campaign,
            )
            return {r["source"]: r["cnt"] for r in rows}

    async def set_gift_application_reminder(self, application_id: int) -> None:
        async with self.get_connection() as conn:
            await conn.execute(
                """
                UPDATE gift_application
                SET reminder_sent_at = NOW(), updated_at = NOW()
                WHERE id = $1
                """,
                application_id,
            )

    async def list_draft_gift_applications_for_reminder(
        self, *, campaign: str = CAMPAIGN_ID, hours: int = 24
    ) -> List[Dict[str, Any]]:
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM gift_application
                WHERE campaign = $1
                  AND status = 'draft'
                  AND eligible
                  AND reminder_sent_at IS NULL
                  AND started_at <= NOW() - ($2 || ' hours')::interval
                ORDER BY started_at
                LIMIT 200
                """,
                campaign,
                str(hours),
            )
            return [dict(r) for r in rows]

    async def expire_stale_gift_drafts(
        self, *, campaign: str = CAMPAIGN_ID, hours: int = 48
    ) -> List[Dict[str, Any]]:
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                UPDATE gift_application
                SET status = 'expired', updated_at = NOW()
                WHERE campaign = $1
                  AND status = 'draft'
                  AND started_at <= NOW() - ($2 || ' hours')::interval
                RETURNING *
                """,
                campaign,
                str(hours),
            )
            return [dict(r) for r in rows]

    async def cancel_gift_application(self, application_id: int) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                """
                UPDATE gift_application
                SET status = 'cancelled',
                    q1_about = NULL,
                    q2_why = NULL,
                    q3_ready = NULL,
                    rules_accepted = FALSE,
                    updated_at = NOW()
                WHERE id = $1 AND status = 'draft'
                RETURNING *
                """,
                application_id,
            )
            return dict(row) if row else None

    async def get_or_create_gift_campaign_state(
        self, *, campaign: str = CAMPAIGN_ID
    ) -> Dict[str, Any]:
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO gift_campaign_state (campaign)
                VALUES ($1)
                ON CONFLICT (campaign) DO UPDATE SET campaign = EXCLUDED.campaign
                RETURNING *
                """,
                campaign,
            )
            return dict(row)

    async def update_gift_campaign_state(
        self,
        *,
        campaign: str = CAMPAIGN_ID,
        stage: Optional[int] = None,
        club_cohort: Optional[str] = None,
        mailing_paused: Optional[bool] = None,
        waves_paused: Optional[bool] = None,
        started: bool = False,
        finished: bool = False,
        notes: Optional[str] = None,
        alerts_json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        sets = ["updated_at = NOW()"]
        args: List[Any] = [campaign]
        idx = 2
        if stage is not None:
            sets.append(f"stage = ${idx}")
            args.append(stage)
            idx += 1
        if club_cohort is not None:
            sets.append(f"club_cohort = ${idx}")
            args.append(club_cohort)
            idx += 1
        if mailing_paused is not None:
            sets.append(f"mailing_paused = ${idx}")
            args.append(mailing_paused)
            idx += 1
        if waves_paused is not None:
            sets.append(f"waves_paused = ${idx}")
            args.append(waves_paused)
            idx += 1
        if started:
            sets.append("started_at = COALESCE(started_at, NOW())")
        if finished:
            sets.append("finished_at = NOW()")
        if notes is not None:
            sets.append(f"notes = ${idx}")
            args.append(notes)
            idx += 1
        if alerts_json is not None:
            sets.append(f"alerts_json = ${idx}::jsonb")
            args.append(json.dumps(alerts_json, ensure_ascii=False))
            idx += 1
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE gift_campaign_state
                SET {', '.join(sets)}
                WHERE campaign = $1
                RETURNING *
                """,
                *args,
            )
            return dict(row) if row else {}

    async def record_gift_mailing_sent(
        self,
        user_id: int,
        *,
        cohort: str,
        stage: int = 1,
        campaign: str = CAMPAIGN_ID,
        delivery: str = "sent",
    ) -> bool:
        async with self.get_connection() as conn:
            r = await conn.execute(
                """
                INSERT INTO gift_mailing_sent
                    (campaign, user_id, cohort, stage, delivery)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (campaign, user_id) DO NOTHING
                """,
                campaign,
                user_id,
                cohort,
                stage,
                delivery,
            )
            return bool(r and r.endswith("1"))

    async def was_gift_mailing_sent(
        self, user_id: int, *, campaign: str = CAMPAIGN_ID
    ) -> bool:
        async with self.get_connection() as conn:
            row = await conn.fetchval(
                """
                SELECT 1 FROM gift_mailing_sent
                WHERE campaign = $1 AND user_id = $2
                LIMIT 1
                """,
                campaign,
                user_id,
            )
            return bool(row)

    async def gift_mailing_stats(
        self, *, campaign: str = CAMPAIGN_ID
    ) -> List[Dict[str, Any]]:
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT cohort, stage,
                       COUNT(*)::int AS sent,
                       COUNT(*) FILTER (WHERE delivery = 'blocked')::int AS blocked
                FROM gift_mailing_sent
                WHERE campaign = $1
                GROUP BY cohort, stage
                ORDER BY stage, cohort
                """,
                campaign,
            )
            return [dict(r) for r in rows]

    async def gift_cohort_report(
        self, *, campaign: str = CAMPAIGN_ID
    ) -> List[Dict[str, Any]]:
        """Отчёт по когортам K1–K3: сколько уже в порциях, сколько осталось, факт доставки."""
        async with self.get_connection() as conn:
            invited_rows = await conn.fetch(
                """
                SELECT cohort, COUNT(*)::int AS invited
                FROM gift_mailing_sent
                WHERE campaign = $1 AND cohort IN ('K1', 'K2', 'K3')
                GROUP BY cohort
                """,
                campaign,
            )
            invited = {r["cohort"]: int(r["invited"]) for r in invited_rows}

            remaining_rows = await conn.fetch(
                """
                WITH paid AS (
                    SELECT DISTINCT user_id FROM payments WHERE status = 'succeeded'
                ),
                lic AS (
                    SELECT DISTINCT user_id FROM license
                ),
                d AS (
                    SELECT u.user_id,
                           u.created_at AS reg,
                           count(DISTINCT (m.created_at AT TIME ZONE 'Europe/Moscow')::date)
                             FILTER (
                               WHERE m.sender_type = 'user'
                                 AND COALESCE(m.chat_type, 'private') = 'private'
                             ) AS days,
                           max(m.created_at) FILTER (WHERE m.sender_type = 'user') AS last
                    FROM users u
                    LEFT JOIN messages m ON m.user_id = u.user_id
                    WHERE COALESCE(u.is_active, TRUE)
                      AND u.user_id NOT IN (SELECT user_id FROM paid)
                      AND u.user_id NOT IN (SELECT user_id FROM lic)
                      AND u.user_id NOT IN (
                          SELECT user_id FROM gift_mailing_sent WHERE campaign = $1
                      )
                      AND u.user_id NOT IN (
                          SELECT user_id FROM gift_application WHERE campaign = $1
                      )
                      AND u.user_id NOT IN (
                          SELECT ma.user_id
                          FROM mailing_audience ma
                          JOIN mailing_campaigns mc ON mc.id = ma.campaign_id
                          WHERE mc.name LIKE 'gift-2026-09%'
                            AND mc.status IN ('planned', 'running', 'completed')
                      )
                    GROUP BY 1, 2
                ),
                labeled AS (
                    SELECT
                           CASE
                             WHEN reg >= NOW() - interval '30 days' THEN NULL
                             WHEN days >= 2 AND last > NOW() - interval '60 days' THEN 'K1'
                             WHEN days >= 2 THEN 'K2'
                             ELSE 'K3'
                           END AS cohort
                    FROM d
                )
                SELECT cohort, COUNT(*)::int AS remaining
                FROM labeled
                WHERE cohort IS NOT NULL
                GROUP BY cohort
                """,
                campaign,
            )
            remaining = {r["cohort"]: int(r["remaining"]) for r in remaining_rows}

            delivery_rows = await conn.fetch(
                """
                SELECT cohort,
                       SUM(delivered)::int AS delivered,
                       SUM(blocked)::int AS blocked,
                       SUM(audience)::int AS audience,
                       COUNT(*)::int AS campaigns,
                       BOOL_OR(is_running) AS any_running,
                       BOOL_OR(is_planned) AS any_planned
                FROM (
                    SELECT
                        CASE
                            WHEN name LIKE '%Т1 K1%' THEN 'K1'
                            WHEN name LIKE '%Т1 K2%' THEN 'K2'
                            WHEN name LIKE '%Т1 K3%' THEN 'K3'
                            WHEN name LIKE '%Т1 TEST%' THEN 'TEST'
                            ELSE NULL
                        END AS cohort,
                        COALESCE(sent_count, 0) AS delivered,
                        COALESCE(blocked_count, 0) AS blocked,
                        (SELECT COUNT(*)::int FROM mailing_audience ma
                         WHERE ma.campaign_id = mc.id) AS audience,
                        (status = 'running') AS is_running,
                        (status = 'planned') AS is_planned
                    FROM mailing_campaigns mc
                    WHERE name LIKE 'gift-2026-09%'
                ) t
                WHERE cohort IS NOT NULL
                GROUP BY cohort
                """
            )
            delivery = {r["cohort"]: dict(r) for r in delivery_rows}

        out: List[Dict[str, Any]] = []
        for c in ("K1", "K2", "K3"):
            d = delivery.get(c) or {}
            inv = int(invited.get(c) or 0)
            rem = int(remaining.get(c) or 0)
            out.append(
                {
                    "cohort": c,
                    "invited": inv,
                    "remaining": rem,
                    "pool_now": inv + rem,
                    "delivered": int(d.get("delivered") or 0),
                    "blocked": int(d.get("blocked") or 0),
                    "audience": int(d.get("audience") or 0),
                    "campaigns": int(d.get("campaigns") or 0),
                    "running": bool(d.get("any_running")),
                    "planned": bool(d.get("any_planned")),
                }
            )
        return out

    async def list_club_gift_cohort_candidates(
        self, *, cohort: str, limit: int = 300, campaign: str = CAMPAIGN_ID
    ) -> List[int]:
        """К1/К2/К3 по ТЗ АНК-6; исключает уже получивших Т1 и подавших заявку."""
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                WITH paid AS (
                    SELECT DISTINCT user_id FROM payments WHERE status = 'succeeded'
                ),
                lic AS (
                    SELECT DISTINCT user_id FROM license
                ),
                d AS (
                    SELECT u.user_id,
                           u.created_at AS reg,
                           count(DISTINCT (m.created_at AT TIME ZONE 'Europe/Moscow')::date)
                             FILTER (
                               WHERE m.sender_type = 'user'
                                 AND COALESCE(m.chat_type, 'private') = 'private'
                             ) AS days,
                           max(m.created_at) FILTER (WHERE m.sender_type = 'user') AS last
                    FROM users u
                    LEFT JOIN messages m ON m.user_id = u.user_id
                    WHERE COALESCE(u.is_active, TRUE)
                      AND u.user_id NOT IN (SELECT user_id FROM paid)
                      AND u.user_id NOT IN (SELECT user_id FROM lic)
                      AND u.user_id NOT IN (
                          SELECT user_id FROM gift_mailing_sent WHERE campaign = $2
                      )
                      AND u.user_id NOT IN (
                          SELECT user_id FROM gift_application WHERE campaign = $2
                      )
                      AND u.user_id NOT IN (
                          SELECT ma.user_id
                          FROM mailing_audience ma
                          JOIN mailing_campaigns mc ON mc.id = ma.campaign_id
                          WHERE mc.name LIKE 'gift-2026-09%'
                            AND mc.status IN ('planned', 'running', 'completed')
                      )
                    GROUP BY 1, 2
                ),
                labeled AS (
                    SELECT user_id, last,
                           CASE
                             WHEN reg >= NOW() - interval '30 days' THEN NULL
                             WHEN days >= 2 AND last > NOW() - interval '60 days' THEN 'K1'
                             WHEN days >= 2 THEN 'K2'
                             ELSE 'K3'
                           END AS cohort
                    FROM d
                )
                SELECT user_id
                FROM labeled
                WHERE cohort = $1
                ORDER BY last DESC NULLS LAST, user_id
                LIMIT $3
                """,
                cohort,
                campaign,
                limit,
            )
            return [int(r["user_id"]) for r in rows]

    async def count_queued_by_source(
        self, *, campaign: str = CAMPAIGN_ID
    ) -> Dict[str, int]:
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT source, COUNT(*)::int AS cnt
                FROM gift_application
                WHERE campaign = $1 AND status = 'queued' AND verdict = 'pass'
                GROUP BY source
                """,
                campaign,
            )
            return {r["source"]: r["cnt"] for r in rows}
