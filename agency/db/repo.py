"""Репозиторий agency PG."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence

import asyncpg


def _j(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


class AgencyRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def upsert_run(
        self, agent_id: str, run_date: date, *, status: str = "running"
    ) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO runs (agent_id, run_date, status)
                VALUES ($1, $2, $3)
                ON CONFLICT (agent_id, run_date) DO UPDATE SET
                    started_at = NOW(),
                    finished_at = NULL,
                    status = EXCLUDED.status,
                    error_text = NULL,
                    meta_json = '{}'::jsonb
                RETURNING id
                """,
                agent_id,
                run_date,
                status,
            )
            return int(row["id"])

    async def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        error_text: str = "",
        meta: Optional[dict] = None,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE runs
                SET finished_at = NOW(),
                    status = $2,
                    error_text = NULLIF($3, ''),
                    meta_json = COALESCE($4::jsonb, meta_json)
                WHERE id = $1
                """,
                run_id,
                status,
                error_text or "",
                _j(meta or {}),
            )

    async def put_fact(
        self,
        *,
        fact_date: date,
        fact_key: str,
        source_system: str,
        value_num: Optional[float] = None,
        value_text: str = "",
        value_json: Optional[dict] = None,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO shared_facts (
                    fact_date, fact_key, source_system,
                    value_num, value_text, value_json
                ) VALUES ($1,$2,$3,$4,$5,$6::jsonb)
                ON CONFLICT (fact_date, fact_key, source_system) DO UPDATE SET
                    value_num = EXCLUDED.value_num,
                    value_text = EXCLUDED.value_text,
                    value_json = EXCLUDED.value_json,
                    created_at = NOW()
                """,
                fact_date,
                fact_key,
                source_system,
                value_num,
                value_text or "",
                _j(value_json or {}),
            )

    async def get_facts(
        self, fact_date: date, keys: Optional[Sequence[str]] = None
    ) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            if keys:
                rows = await conn.fetch(
                    """
                    SELECT * FROM shared_facts
                    WHERE fact_date = $1 AND fact_key = ANY($2::text[])
                    ORDER BY fact_key
                    """,
                    fact_date,
                    list(keys),
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM shared_facts WHERE fact_date = $1 ORDER BY fact_key",
                    fact_date,
                )
        return [dict(r) for r in rows]

    async def get_fact_history(
        self, fact_key: str, source_system: str, since: date, until: date
    ) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT fact_date, value_num, value_json
                FROM shared_facts
                WHERE fact_key = $1 AND source_system = $2
                  AND fact_date >= $3 AND fact_date <= $4
                ORDER BY fact_date
                """,
                fact_key,
                source_system,
                since,
                until,
            )
        return [dict(r) for r in rows]

    async def add_artifact(
        self,
        *,
        run_id: int,
        agent_id: str,
        kind: str,
        title: str = "",
        body_text: str = "",
        body_json: Optional[dict] = None,
    ) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO artifacts (run_id, agent_id, kind, title, body_text, body_json)
                VALUES ($1,$2,$3,$4,$5,$6::jsonb)
                RETURNING id
                """,
                run_id,
                agent_id,
                kind,
                title or "",
                body_text or "",
                _j(body_json or {}),
            )
            return int(row["id"])

    async def add_recommendation(
        self,
        *,
        agent_id: str,
        run_id: int,
        created_on: date,
        title: str,
        body: str,
        evidence: str = "",
        target_system: str = "biblia",
        priority: int = 2,
        measure_after_days: int = 7,
        meta: Optional[dict] = None,
    ) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO recommendations (
                    agent_id, run_id, created_on, title, body, evidence,
                    target_system, priority, measure_after_days, meta_json
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
                RETURNING id
                """,
                agent_id,
                run_id,
                created_on,
                title[:300],
                body,
                evidence or "",
                target_system,
                priority,
                measure_after_days,
                _j(meta or {}),
            )
            return int(row["id"])

    async def list_recommendations(
        self,
        *,
        agent_id: Optional[str] = None,
        statuses: Optional[Sequence[str]] = None,
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        clauses = ["TRUE"]
        args: list = []
        if agent_id:
            args.append(agent_id)
            clauses.append(f"agent_id = ${len(args)}")
        if statuses:
            args.append(list(statuses))
            clauses.append(f"status = ANY(${len(args)}::text[])")
        args.append(limit)
        sql = f"""
            SELECT * FROM recommendations
            WHERE {' AND '.join(clauses)}
            ORDER BY created_on DESC, id DESC
            LIMIT ${len(args)}
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]

    async def get_recommendation(self, rec_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM recommendations WHERE id = $1", rec_id
            )
        return dict(row) if row else None

    async def set_recommendation_status(
        self,
        rec_id: int,
        status: str,
        *,
        actor_user_id: Optional[int] = None,
        note: str = "",
    ) -> bool:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    UPDATE recommendations
                    SET status = $2,
                        updated_at = NOW(),
                        shipped_at = CASE WHEN $2 = 'shipped' THEN NOW() ELSE shipped_at END,
                        measured_at = CASE WHEN $2 = 'measured' THEN NOW() ELSE measured_at END
                    WHERE id = $1
                    RETURNING id
                    """,
                    rec_id,
                    status,
                )
                if not row:
                    return False
                action = {
                    "accepted": "accept",
                    "rejected": "reject",
                    "shipped": "ship",
                    "abandoned": "abandon",
                }.get(status, status)
                await conn.execute(
                    """
                    INSERT INTO approvals (recommendation_id, actor_user_id, action, note)
                    VALUES ($1, $2, $3, $4)
                    """,
                    rec_id,
                    actor_user_id,
                    action,
                    note or "",
                )
        return True

    async def upsert_data_gap(
        self,
        *,
        gap_key: str,
        description: str,
        agent_id: str = "bible_bot_manager",
        severity: str = "medium",
        on: Optional[date] = None,
        meta: Optional[dict] = None,
    ) -> None:
        on = on or date.today()
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO data_gaps (
                    agent_id, gap_key, description, severity,
                    first_seen_on, last_seen_on, meta_json
                ) VALUES ($1,$2,$3,$4,$5,$5,$6::jsonb)
                ON CONFLICT (gap_key) DO UPDATE SET
                    description = EXCLUDED.description,
                    severity = EXCLUDED.severity,
                    last_seen_on = EXCLUDED.last_seen_on,
                    meta_json = EXCLUDED.meta_json,
                    status = CASE
                        WHEN data_gaps.status = 'closed' THEN 'open'
                        ELSE data_gaps.status
                    END
                """,
                agent_id,
                gap_key,
                description,
                severity,
                on,
                _j(meta or {}),
            )

    async def close_data_gap(self, gap_key: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE data_gaps
                SET status = 'closed',
                    last_seen_on = (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Moscow')::date
                WHERE gap_key = $1 AND status <> 'closed'
                """,
                gap_key,
            )

    async def register_brief_message(
        self,
        *,
        run_id: int,
        agent_id: str,
        chat_id: int,
        telegram_message_id: int,
        chunk_index: int = 0,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO brief_messages (
                    run_id, agent_id, chat_id, telegram_message_id, chunk_index
                ) VALUES ($1,$2,$3,$4,$5)
                ON CONFLICT (chat_id, telegram_message_id) DO UPDATE SET
                    run_id = EXCLUDED.run_id
                """,
                run_id,
                agent_id,
                chat_id,
                telegram_message_id,
                chunk_index,
            )

    async def find_brief_by_reply(
        self, chat_id: int, reply_to_message_id: int
    ) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT bm.*, r.run_date, r.status AS run_status
                FROM brief_messages bm
                JOIN runs r ON r.id = bm.run_id
                WHERE bm.chat_id = $1 AND bm.telegram_message_id = $2
                """,
                chat_id,
                reply_to_message_id,
            )
        return dict(row) if row else None

    async def get_run_brief_text(self, run_id: int) -> str:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT body_text FROM artifacts
                WHERE run_id = $1 AND kind = 'brief_md'
                ORDER BY id DESC LIMIT 1
                """,
                run_id,
            )
        return (row["body_text"] if row else "") or ""

    async def get_run_recommendations(self, run_id: int) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM recommendations
                WHERE run_id = $1
                ORDER BY id
                """,
                run_id,
            )
        return [dict(r) for r in rows]

    async def add_brief_discussion(
        self,
        *,
        run_id: Optional[int],
        agent_id: str,
        chat_id: int,
        reply_to_message_id: int,
        user_id: int,
        user_text: str,
        assistant_text: str,
        actions: Optional[dict] = None,
    ) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO brief_discussions (
                    run_id, agent_id, chat_id, reply_to_message_id,
                    user_id, user_text, assistant_text, actions_json
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)
                RETURNING id
                """,
                run_id,
                agent_id,
                chat_id,
                reply_to_message_id,
                user_id,
                user_text,
                assistant_text,
                _j(actions or {}),
            )
            return int(row["id"])

    async def recent_discussions_for_run(
        self, run_id: int, limit: int = 10
    ) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT user_text, assistant_text, created_at
                FROM brief_discussions
                WHERE run_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                run_id,
                limit,
            )
        return [dict(r) for r in rows]

    async def open_gaps(self, limit: int = 20) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM data_gaps
                WHERE status = 'open'
                ORDER BY last_seen_on DESC
                LIMIT $1
                """,
                limit,
            )
        return [dict(r) for r in rows]

    async def add_handoff(
        self,
        *,
        from_agent_id: str,
        to_agent_id: str,
        run_id: Optional[int],
        subject: str,
        body: str,
        payload: Optional[dict] = None,
    ) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO handoffs (
                    from_agent_id, to_agent_id, run_id, subject, body, payload_json
                ) VALUES ($1,$2,$3,$4,$5,$6::jsonb)
                RETURNING id
                """,
                from_agent_id,
                to_agent_id,
                run_id,
                subject[:300],
                body,
                _j(payload or {}),
            )
            return int(row["id"])

    async def pending_handoffs(self, to_agent_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM handoffs
                WHERE to_agent_id = $1 AND status = 'pending'
                ORDER BY created_at DESC
                LIMIT $2
                """,
                to_agent_id,
                limit,
            )
        return [dict(r) for r in rows]

    async def add_external_signal(
        self,
        *,
        run_id: int,
        agent_id: str,
        signal_date: date,
        summary: str,
        source_url: str = "",
        title: str = "",
        relevance: str = "",
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO external_signals (
                    run_id, agent_id, signal_date, source_url, title, summary, relevance
                ) VALUES ($1,$2,$3,$4,$5,$6,$7)
                """,
                run_id,
                agent_id,
                signal_date,
                source_url or None,
                title or None,
                summary,
                relevance or "",
            )

    async def add_content_event(
        self,
        *,
        platform: str,
        published_at: Optional[datetime],
        url: str = "",
        title: str = "",
        body_excerpt: str = "",
        ref_key: str = "",
        source: str = "manual",
        meta: Optional[dict] = None,
    ) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO content_events (
                    platform, published_at, url, title, body_excerpt,
                    ref_key, source, meta_json
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)
                RETURNING id
                """,
                platform,
                published_at,
                url or None,
                title or None,
                body_excerpt or None,
                ref_key or None,
                source,
                _j(meta or {}),
            )
            return int(row["id"])

    async def content_events_around(
        self, center: datetime, days: int = 3, limit: int = 40
    ) -> List[Dict[str, Any]]:
        from datetime import timedelta

        delta = timedelta(days=max(0, int(days)))
        start = center - delta
        end = center + delta
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM content_events
                WHERE published_at IS NOT NULL
                  AND published_at >= $1
                  AND published_at <= $2
                ORDER BY published_at DESC
                LIMIT $3
                """,
                start,
                end,
                limit,
            )
        return [dict(r) for r in rows]

    async def log_llm_call(
        self,
        *,
        run_id: Optional[int],
        agent_id: str,
        provider: str,
        model: str,
        role_in_panel: str,
        has_web: bool = False,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        latency_ms: Optional[int] = None,
        ok: bool = True,
        error_text: str = "",
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO llm_calls (
                    run_id, agent_id, provider, model, role_in_panel,
                    has_web, prompt_tokens, completion_tokens, latency_ms,
                    ok, error_text
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                """,
                run_id,
                agent_id,
                provider,
                model,
                role_in_panel,
                has_web,
                prompt_tokens,
                completion_tokens,
                latency_ms,
                ok,
                error_text or None,
            )

    async def add_draft_pr(
        self,
        *,
        agent_id: str,
        run_id: Optional[int],
        recommendation_id: Optional[int],
        patch_text: str,
        branch_name: str = "",
        repo: str = "biblia",
        status: str = "draft_local",
        pr_url: str = "",
    ) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO draft_prs (
                    recommendation_id, run_id, agent_id, repo,
                    branch_name, pr_url, status, patch_text
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                RETURNING id
                """,
                recommendation_id,
                run_id,
                agent_id,
                repo,
                branch_name or None,
                pr_url or None,
                status,
                patch_text,
            )
            return int(row["id"])

    async def add_outcome(
        self,
        *,
        recommendation_id: int,
        measured_on: date,
        kpi_before: dict,
        kpi_after: dict,
        verdict: str,
        notes: str = "",
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO recommendation_outcomes (
                    recommendation_id, measured_on,
                    kpi_before_json, kpi_after_json, verdict, notes
                ) VALUES ($1,$2,$3::jsonb,$4::jsonb,$5,$6)
                """,
                recommendation_id,
                measured_on,
                _j(kpi_before),
                _j(kpi_after),
                verdict,
                notes or "",
            )
            await conn.execute(
                """
                UPDATE recommendations
                SET status = 'measured', measured_at = NOW(), updated_at = NOW()
                WHERE id = $1
                """,
                recommendation_id,
            )
