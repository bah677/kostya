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

    async def fetch_revokable_club_invites_for_user(
        self, user_id: int
    ) -> List[Dict[str, Any]]:
        """Неиспользованные и не отозванные инвайты пользователя (для отзыва перед новой ссылкой)."""
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT id, invite_link FROM club_invites
                WHERE user_id = $1 AND used = FALSE AND revoked = FALSE
                ORDER BY id
                """,
                user_id,
            )
            return [dict(r) for r in rows]

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

    async def record_club_member_exclusion(
        self,
        user_id: int,
        *,
        reason: str = "unknown",
        source: str = "unknown",
    ) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO club_member_exclusions (user_id, reason, source)
                    VALUES ($1, $2, $3)
                    """,
                    user_id,
                    reason,
                    source,
                )
        except Exception as e:
            logger.error(
                "record_club_member_exclusion uid=%s: %s", user_id, e
            )

    async def get_last_club_exclusion_before(
        self, user_id: int, before: datetime
    ) -> Optional[datetime]:
        try:
            async with self.get_connection() as conn:
                return await conn.fetchval(
                    """
                    SELECT excluded_at FROM club_member_exclusions
                    WHERE user_id = $1 AND excluded_at < $2
                    ORDER BY excluded_at DESC
                    LIMIT 1
                    """,
                    user_id,
                    before,
                )
        except Exception as e:
            logger.error(
                "get_last_club_exclusion_before uid=%s: %s", user_id, e
            )
            return None

    async def get_last_club_exclusion_at(
        self, user_id: int, *, before: Optional[datetime] = None
    ) -> Optional[datetime]:
        """Последнее исключение из группы клуба (опционально — строго до ``before``)."""
        try:
            async with self.get_connection() as conn:
                if before is not None:
                    return await conn.fetchval(
                        """
                        SELECT excluded_at FROM club_member_exclusions
                        WHERE user_id = $1 AND excluded_at < $2
                        ORDER BY excluded_at DESC
                        LIMIT 1
                        """,
                        user_id,
                        before,
                    )
                return await conn.fetchval(
                    """
                    SELECT excluded_at FROM club_member_exclusions
                    WHERE user_id = $1
                    ORDER BY excluded_at DESC
                    LIMIT 1
                    """,
                    user_id,
                )
        except Exception as e:
            logger.error("get_last_club_exclusion_at uid=%s: %s", user_id, e)
            return None

    async def list_club_member_cache_user_ids(self) -> List[int]:
        async with self.get_connection() as conn:
            rows = await conn.fetch(
                "SELECT user_id FROM club_group_member_cache ORDER BY user_id"
            )
            return [r["user_id"] for r in rows]
