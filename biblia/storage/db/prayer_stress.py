"""Mixin: словарь ударений для озвучки молитв и очередь модерации."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ENSURE_SQL = """
CREATE TABLE IF NOT EXISTS prayer_stress_dictionary (
    base_word TEXT PRIMARY KEY,
    accented_word TEXT NOT NULL,
    source_word TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_by BIGINT
);

CREATE TABLE IF NOT EXISTS prayer_stress_proposals (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    username TEXT,
    first_name TEXT,
    chat_id BIGINT,
    source_word TEXT NOT NULL,
    base_word TEXT NOT NULL,
    accented_word TEXT NOT NULL,
    sample_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    admin_chat_id BIGINT,
    admin_voice_message_id BIGINT,
    admin_text_message_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at TIMESTAMPTZ,
    decided_by BIGINT
);

CREATE INDEX IF NOT EXISTS idx_prayer_stress_proposals_status
    ON prayer_stress_proposals(status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_prayer_stress_proposals_admin_voice
    ON prayer_stress_proposals(admin_voice_message_id);
"""


class PrayerStressMixin:
    async def ensure_prayer_stress_schema(self) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(_ENSURE_SQL)
        except Exception as e:
            logger.error("❌ ensure prayer_stress schema failed: %s", e)
            raise

    async def get_prayer_stress_dictionary(self) -> Dict[str, str]:
        try:
            await self.ensure_prayer_stress_schema()
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT base_word, accented_word
                    FROM prayer_stress_dictionary
                    ORDER BY base_word
                    """
                )
            return {
                str(r["base_word"]): str(r["accented_word"])
                for r in rows
                if r["base_word"] and r["accented_word"]
            }
        except Exception as e:
            logger.error("❌ Failed to load prayer stress dictionary: %s", e)
            return {}

    async def upsert_prayer_stress_dictionary_word(
        self,
        *,
        base_word: str,
        accented_word: str,
        source_word: str,
        approved_by: Optional[int] = None,
    ) -> bool:
        try:
            await self.ensure_prayer_stress_schema()
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO prayer_stress_dictionary (
                        base_word, accented_word, source_word, approved_by
                    )
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (base_word) DO UPDATE SET
                        accented_word = EXCLUDED.accented_word,
                        source_word = EXCLUDED.source_word,
                        approved_by = EXCLUDED.approved_by,
                        updated_at = NOW()
                    """,
                    base_word,
                    accented_word,
                    source_word,
                    approved_by,
                )
            return True
        except Exception as e:
            logger.error("❌ Failed to upsert prayer stress word %s: %s", base_word, e)
            return False

    async def create_prayer_stress_proposal(
        self,
        *,
        user_id: int,
        username: str,
        first_name: str,
        chat_id: int,
        source_word: str,
        base_word: str,
        accented_word: str,
        sample_text: str,
    ) -> Optional[int]:
        try:
            await self.ensure_prayer_stress_schema()
            async with self.get_connection() as conn:
                row_id = await conn.fetchval(
                    """
                    INSERT INTO prayer_stress_proposals (
                        user_id, username, first_name, chat_id,
                        source_word, base_word, accented_word, sample_text
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    RETURNING id
                    """,
                    user_id,
                    username,
                    first_name,
                    chat_id,
                    source_word,
                    base_word,
                    accented_word,
                    sample_text,
                )
            return int(row_id) if row_id is not None else None
        except Exception as e:
            logger.error("❌ Failed to create prayer stress proposal: %s", e)
            return None

    async def set_prayer_stress_proposal_admin_message_ids(
        self,
        proposal_id: int,
        *,
        admin_chat_id: int,
        admin_voice_message_id: Optional[int] = None,
        admin_text_message_id: Optional[int] = None,
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE prayer_stress_proposals
                       SET admin_chat_id = $2,
                           admin_voice_message_id = COALESCE($3, admin_voice_message_id),
                           admin_text_message_id = COALESCE($4, admin_text_message_id)
                     WHERE id = $1
                    """,
                    proposal_id,
                    admin_chat_id,
                    admin_voice_message_id,
                    admin_text_message_id,
                )
            return True
        except Exception as e:
            logger.error("❌ Failed to save prayer stress admin message ids: %s", e)
            return False

    async def get_prayer_stress_proposal(self, proposal_id: int) -> Optional[Dict[str, Any]]:
        try:
            await self.ensure_prayer_stress_schema()
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM prayer_stress_proposals WHERE id = $1",
                    proposal_id,
                )
            return dict(row) if row else None
        except Exception as e:
            logger.error("❌ Failed to get prayer stress proposal %s: %s", proposal_id, e)
            return None

    async def decide_prayer_stress_proposal(
        self,
        proposal_id: int,
        *,
        status: str,
        decided_by: Optional[int] = None,
    ) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE prayer_stress_proposals
                       SET status = $2,
                           decided_at = NOW(),
                           decided_by = $3
                     WHERE id = $1
                    """,
                    proposal_id,
                    status,
                    decided_by,
                )
            return True
        except Exception as e:
            logger.error("❌ Failed to decide prayer stress proposal %s: %s", proposal_id, e)
            return False

    async def list_pending_prayer_stress_proposals(
        self, limit: int = 50
    ) -> List[Dict[str, Any]]:
        try:
            await self.ensure_prayer_stress_schema()
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT *
                    FROM prayer_stress_proposals
                    WHERE status = 'pending'
                    ORDER BY created_at ASC
                    LIMIT $1
                    """,
                    limit,
                )
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("❌ Failed to list pending prayer stress proposals: %s", e)
            return []
