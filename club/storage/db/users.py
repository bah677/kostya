"""
Mixin: работа с пользователями (таблица `users`).
Счётчик заданных вопросов, онбординг, профиль/ДР, базовый CRUD,
лицензионная отметка (полная работа с лицензиями в LicensesMixin).
"""

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class UsersMixin:

    # =====================================================
    # Счётчик использованных вопросов
    # =====================================================

    async def get_user_usage_stats(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Возвращает счётчик заданных вопросов."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT questions_asked FROM users WHERE user_id = $1",
                    user_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get usage stats for user_id={user_id}: {e}")
            return None

    async def increment_questions_asked(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "UPDATE users SET questions_asked = questions_asked + 1 WHERE user_id = $1",
                    user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to increment questions for user_id={user_id}: {e}")
            return False

    # =====================================================
    # Лицензионная отметка (использует таблицу license, но смысл — на пользователе)
    # Полная работа с лицензиями в LicensesMixin.
    # =====================================================

    async def check_user_license(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                result = await conn.fetchval(
                    "SELECT 1 FROM license WHERE user_id = $1 AND status = $2",
                    user_id, "active",
                )
                return result is not None
        except Exception as e:
            logger.error(f"❌ Failed to check license for user_id={user_id}: {e}")
            return False

    async def add_user_license(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "INSERT INTO license (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
                    user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to add license for user_id={user_id}: {e}")
            return False

    # =====================================================
    # Онбординг
    # =====================================================

    async def set_onboarding_complete(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "UPDATE users SET onboarding_complete = TRUE WHERE user_id = $1",
                    user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to set onboarding complete for user_id={user_id}: {e}")
            return False

    async def is_onboarding_complete(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                result = await conn.fetchval(
                    "SELECT onboarding_complete FROM users WHERE user_id = $1",
                    user_id,
                )
                return bool(result)
        except Exception as e:
            logger.error(f"❌ Failed to check onboarding status for user_id={user_id}: {e}")
            return False

    # =====================================================
    # Базовый CRUD пользователей
    # =====================================================

    async def add_or_update_user(self, user_data: Dict[str, Any]) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO users
                    (user_id, username, first_name, last_name, language_code, is_premium, last_activity)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (user_id)
                    DO UPDATE SET
                        username = EXCLUDED.username,
                        first_name = EXCLUDED.first_name,
                        last_name = EXCLUDED.last_name,
                        language_code = EXCLUDED.language_code,
                        is_premium = EXCLUDED.is_premium,
                        last_activity = EXCLUDED.last_activity,
                        is_active = TRUE
                    """,
                    user_data["user_id"],
                    user_data.get("username"),
                    user_data.get("first_name"),
                    user_data.get("last_name"),
                    user_data.get("language_code"),
                    user_data.get("is_premium", False),
                    datetime.now(),
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to save user {user_data.get('user_id')}: {e}")
            return False

    async def update_openai_thread(self, user_id: int, thread_id: str) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "UPDATE users SET openai_thread_id = $1 WHERE user_id = $2",
                    thread_id, user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update thread for user_id={user_id}: {e}")
            return False

    async def update_user_activity(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "UPDATE users SET last_activity = $1 WHERE user_id = $2",
                    datetime.now(), user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update activity for user_id={user_id}: {e}")
            return False

    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM users WHERE user_id = $1",
                    user_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get user {user_id}: {e}")
            return None

    async def get_all_users(self) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch("SELECT * FROM users ORDER BY created_at DESC")
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get users: {e}")
            return []

    async def get_active_users(self, days: int = 30) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                # Используем параметр через make_interval, чтобы не подставлять через f-строку
                rows = await conn.fetch(
                    """
                    SELECT * FROM users
                    WHERE last_activity >= NOW() - make_interval(days => $1)
                    ORDER BY last_activity DESC
                    """,
                    days,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get active users: {e}")
            return []

    # =====================================================
    # Профиль / дата рождения / таймзона / сброс / рейтинг
    # =====================================================

    async def update_user_birthday(self, user_id: int, birthday: date) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "UPDATE users SET bd = $1 WHERE user_id = $2",
                    birthday, user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update birthday for user_id={user_id}: {e}")
            return False

    async def update_user_profile(self, user_id: int, profile: str) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "UPDATE users SET profile = $1 WHERE user_id = $2",
                    profile, user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update profile for user_id={user_id}: {e}")
            return False

    async def get_user_birthday(self, user_id: int) -> Optional[date]:
        try:
            async with self.get_connection() as conn:
                return await conn.fetchval(
                    "SELECT bd FROM users WHERE user_id = $1",
                    user_id,
                )
        except Exception as e:
            logger.error(f"❌ Failed to get birthday for user_id={user_id}: {e}")
            return None

    async def get_user_profile(self, user_id: int) -> Optional[str]:
        try:
            async with self.get_connection() as conn:
                return await conn.fetchval(
                    "SELECT profile FROM users WHERE user_id = $1",
                    user_id,
                )
        except Exception as e:
            logger.error(f"❌ Failed to get profile for user_id={user_id}: {e}")
            return None

    async def reset_user_data(self, user_id: int) -> bool:
        """Сбрасывает данные пользователя для повторного онбординга (сохраняет историю)."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE users
                    SET onboarding_complete = FALSE,
                        bd = NULL,
                        profile = NULL,
                        openai_thread_id = NULL,
                        last_activity = NOW()
                    WHERE user_id = $1
                    """,
                    user_id,
                )
                logger.info(f"✅ User onboarding data reset for user_id={user_id}")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to reset user data for user_id={user_id}: {e}")
            return False

    async def save_first_answer_rating(self, user_id: int, rating: str) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE users
                    SET first_answer_rating = $1, rated_at = NOW()
                    WHERE user_id = $2
                    """,
                    rating, user_id,
                )
                logger.info(f"✅ Rating saved for user_id={user_id}: {rating}")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to save rating for user_id={user_id}: {e}")
            return False

    async def update_user_timezone(self, user_id: int, timezone_offset: int) -> bool:
        """Обновляет часовой пояс пользователя (в минутах от UTC)."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "UPDATE users SET timezone_offset = $1 WHERE user_id = $2",
                    timezone_offset, user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update timezone for user_id={user_id}: {e}")
            return False

    # =====================================================
    # Бан / деактивация
    # =====================================================

    async def is_user_banned(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                result = await conn.fetchval(
                    "SELECT is_banned FROM users WHERE user_id = $1",
                    user_id,
                )
                return bool(result)
        except Exception as e:
            logger.error(f"❌ Failed to check ban for user {user_id}: {e}")
            return False

    async def deactivate_user(self, user_id: int) -> bool:
        """Помечает пользователя как неактивного (например, заблокировал бота)."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE users
                    SET is_active = FALSE, updated_at = NOW()
                    WHERE user_id = $1
                    """,
                    user_id,
                )
                logger.info(f"✅ User {user_id} deactivated (blocked bot)")
                return True
        except Exception as e:
            logger.error(f"❌ Failed to deactivate user {user_id}: {e}")
            return False

    async def search_active_club_members(
        self,
        query: str,
        *,
        exclude_user_id: Optional[int] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Ищет участников с активной лицензией по @username, имени или фамилии.
        """
        raw = (query or "").strip().lstrip("@")
        if len(raw) < 2:
            return []
        pattern = f"%{raw}%"
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT
                        u.user_id,
                        u.first_name,
                        u.last_name,
                        u.username,
                        l.expires_at AS license_expires_at
                    FROM users u
                    INNER JOIN license l ON l.user_id = u.user_id
                    WHERE u.is_active IS TRUE
                      AND l.status = 'active'
                      AND l.expires_at > NOW()
                      AND ($3::bigint IS NULL OR u.user_id <> $3)
                      AND (
                          u.username ILIKE $1
                          OR u.first_name ILIKE $2
                          OR u.last_name ILIKE $2
                          OR TRIM(
                              COALESCE(u.first_name, '') || ' ' || COALESCE(u.last_name, '')
                          ) ILIKE $2
                      )
                    ORDER BY
                        CASE WHEN u.username ILIKE $1 THEN 0 ELSE 1 END,
                        u.first_name NULLS LAST,
                        u.user_id
                    LIMIT $4
                    """,
                    raw,
                    pattern,
                    exclude_user_id,
                    limit,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("search_active_club_members failed: %s", e)
            return []

    async def user_has_active_license(self, user_id: int) -> bool:
        lic = await self.get_user_active_license(user_id)
        if not lic or not lic.get("expires_at"):
            return False
        exp = lic["expires_at"]
        if isinstance(exp, datetime):
            return exp > datetime.now()
        return False

    async def save_agent_session_id(self, user_id: int, session_id: str) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "UPDATE users SET agent_session_id = $1 WHERE user_id = $2",
                    session_id, user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to save agent session for user {user_id}: {e}")
            return False

    async def get_agent_session_id(self, user_id: int) -> Optional[str]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT agent_session_id FROM users WHERE user_id = $1",
                    user_id,
                )
                return row["agent_session_id"] if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get agent session for user {user_id}: {e}")
            return None

    async def clear_agent_session_id(self, user_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    "UPDATE users SET agent_session_id = NULL WHERE user_id = $1",
                    user_id,
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to clear agent session for user {user_id}: {e}")
            return False

    async def update_user_contact_fields(
        self,
        user_id: int,
        *,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        email: Optional[str] = None,
    ) -> bool:
        """Обновляет name / phone / email (временный онбординг Насти)."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE users
                       SET name = COALESCE($2, name),
                           phone = COALESCE($3, phone),
                           email = COALESCE($4, email),
                           updated_at = NOW()
                     WHERE user_id = $1
                    """,
                    user_id,
                    name,
                    phone,
                    email,
                )
                return True
        except Exception as e:
            logger.error(
                "❌ Failed to update contact fields for user %s: %s", user_id, e
            )
            return False

    async def get_user_first_bot_seen_at(self, user_id: int) -> Optional[datetime]:
        """Первый /start или регистрация пользователя — что раньше по времени."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        u.created_at AS user_created,
                        (
                            SELECT MIN(il.created_at)
                            FROM interaction_logs il
                            WHERE il.user_id = $1
                              AND il.event_category = 'message'
                              AND COALESCE(il.data->>'text', '') ILIKE '/start%'
                        ) AS first_start_at
                    FROM users u
                    WHERE u.user_id = $1
                    """,
                    user_id,
                )
            if not row:
                return None
            candidates = [row["first_start_at"], row["user_created"]]
            seen = [t for t in candidates if t is not None]
            return min(seen) if seen else None
        except Exception as e:
            logger.error(
                "❌ Failed to get first bot seen for user %s: %s", user_id, e
            )
            return None
