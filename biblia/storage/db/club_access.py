"""
Mixin: закрытый клуб клуба (`club_invites`, `club_group_member_cache`).
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ClubAccessMixin:

    async def insert_club_invite(
        self,
        user_id: int,
        invite_link: str,
        expires_at: datetime,
    ) -> None:
        async with self.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO club_invites (user_id, invite_link, expires_at, created_at)
                VALUES ($1, $2, $3, NOW())
                """,
                user_id,
                invite_link,
                expires_at,
            )
        logger.info(f"✅ Invite record saved for user {user_id}")

    async def fetch_expired_unused_club_invites(self) -> List[Dict[str, Any]]:
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT id, invite_link FROM club_invites
                WHERE used = FALSE AND revoked = FALSE AND expires_at < NOW()
                ORDER BY id
                """
            )
            return [dict(r) for r in rows]

    async def mark_club_invite_revoked(self, invite_id: int) -> None:
        async with self.get_connection() as conn:
            await conn.execute(
                "UPDATE club_invites SET revoked = TRUE WHERE id = $1",
                invite_id,
            )

    async def upsert_club_member_cache(self, user_id: int) -> None:
        async with self.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO club_group_member_cache (user_id, updated_at)
                VALUES ($1, NOW())
                ON CONFLICT (user_id) DO UPDATE SET updated_at = NOW()
                """,
                user_id,
            )

    async def delete_club_member_cache(self, user_id: int) -> None:
        async with self.get_connection() as conn:
            await conn.execute(
                "DELETE FROM club_group_member_cache WHERE user_id = $1",
                user_id,
            )

    async def list_club_member_cache_user_ids(self) -> List[int]:
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                "SELECT user_id FROM club_group_member_cache ORDER BY user_id"
            )
            return [r["user_id"] for r in rows]
