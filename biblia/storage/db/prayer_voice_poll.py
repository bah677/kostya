"""Mixin: оценки голосов молитвы (опрос админов)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ENSURE_SQL = """
CREATE TABLE IF NOT EXISTS prayer_voice_ratings (
    poll_slug       TEXT NOT NULL,
    admin_user_id   BIGINT NOT NULL,
    voice_id        TEXT NOT NULL,
    voice_idx       INTEGER NOT NULL,
    score           SMALLINT NOT NULL CHECK (score BETWEEN 1 AND 5),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (poll_slug, admin_user_id, voice_id)
);

CREATE INDEX IF NOT EXISTS idx_prayer_voice_ratings_poll_voice
    ON prayer_voice_ratings (poll_slug, voice_id);
"""


class PrayerVoicePollMixin:
    async def ensure_prayer_voice_poll_schema(self) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(_ENSURE_SQL)
        except Exception as e:
            logger.error("ensure_prayer_voice_poll_schema: %s", e)
            raise

    async def upsert_prayer_voice_rating(
        self,
        *,
        poll_slug: str,
        admin_user_id: int,
        voice_id: str,
        voice_idx: int,
        score: int,
    ) -> None:
        await self.ensure_prayer_voice_poll_schema()
        async with self.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO prayer_voice_ratings (
                    poll_slug, admin_user_id, voice_id, voice_idx, score, updated_at
                ) VALUES ($1, $2, $3, $4, $5, NOW())
                ON CONFLICT (poll_slug, admin_user_id, voice_id) DO UPDATE SET
                    voice_idx = EXCLUDED.voice_idx,
                    score = EXCLUDED.score,
                    updated_at = NOW()
                """,
                poll_slug,
                int(admin_user_id),
                voice_id,
                int(voice_idx),
                int(score),
            )

    async def get_prayer_voice_rating_for_admin(
        self,
        poll_slug: str,
        admin_user_id: int,
        voice_id: str,
    ) -> Optional[int]:
        await self.ensure_prayer_voice_poll_schema()
        async with self.get_connection() as conn:
            val = await conn.fetchval(
                """
                SELECT score FROM prayer_voice_ratings
                 WHERE poll_slug = $1 AND admin_user_id = $2 AND voice_id = $3
                """,
                poll_slug,
                int(admin_user_id),
                voice_id,
            )
        return int(val) if val is not None else None

    async def list_prayer_voice_ratings_aggregate(
        self, poll_slug: str
    ) -> List[Dict[str, Any]]:
        await self.ensure_prayer_voice_poll_schema()
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT voice_id,
                       MIN(voice_idx) AS voice_idx,
                       COUNT(*)::int AS vote_count,
                       COALESCE(SUM(score), 0)::int AS score_sum,
                       COALESCE(AVG(score), 0)::float AS score_avg
                  FROM prayer_voice_ratings
                 WHERE poll_slug = $1
                 GROUP BY voice_id
                 ORDER BY MIN(voice_idx)
                """,
                poll_slug,
            )
        return [dict(r) for r in rows]

    async def get_prayer_voice_poll_participant_stats(
        self, poll_slug: str, *, voice_count: int
    ) -> Dict[str, int]:
        await self.ensure_prayer_voice_poll_schema()
        async with self.get_connection() as conn:
            row = await conn.fetchrow(
                """
                WITH per_user AS (
                    SELECT admin_user_id, COUNT(*)::int AS rated_n
                      FROM prayer_voice_ratings
                     WHERE poll_slug = $1
                     GROUP BY admin_user_id
                )
                SELECT
                    COALESCE(COUNT(*), 0)::int AS voters,
                    COALESCE(
                        COUNT(*) FILTER (WHERE rated_n >= $2),
                        0
                    )::int AS complete_voters,
                    COALESCE(
                        COUNT(*) FILTER (
                            WHERE rated_n > 0 AND rated_n < $2
                        ),
                        0
                    )::int AS partial_voters,
                    COALESCE(
                        (SELECT COUNT(*)::int FROM prayer_voice_ratings
                          WHERE poll_slug = $1),
                        0
                    ) AS total_ratings
                  FROM per_user
                """,
                poll_slug,
                int(voice_count),
            )
        if not row:
            return {
                "voters": 0,
                "complete_voters": 0,
                "partial_voters": 0,
                "total_ratings": 0,
            }
        return {
            "voters": int(row["voters"] or 0),
            "complete_voters": int(row["complete_voters"] or 0),
            "partial_voters": int(row["partial_voters"] or 0),
            "total_ratings": int(row["total_ratings"] or 0),
        }

    async def list_prayer_voice_ratings_by_admin(
        self, poll_slug: str, admin_user_id: int
    ) -> Dict[str, int]:
        await self.ensure_prayer_voice_poll_schema()
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT voice_id, score
                  FROM prayer_voice_ratings
                 WHERE poll_slug = $1 AND admin_user_id = $2
                """,
                poll_slug,
                int(admin_user_id),
            )
        return {str(r["voice_id"]): int(r["score"]) for r in rows}
