"""
Mixin: рефералы (`referrals`, `ref_keys`) + бонусы и источник перехода.
"""

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Первый /start из логов: отрезаем имя бота после команды.
_START_CMD_RE = re.compile(
    r"^/start(?:@[A-Za-z0-9_]+)?(?:\s+(.*))?$",
    re.IGNORECASE | re.DOTALL,
)


class ReferralsMixin:

    async def get_referral_by_referred_id(self, referred_id: str) -> Optional[Dict[str, Any]]:
        """Запись о реферале по ID реферала (Telegram ID как строка)."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM referrals WHERE referred_id = $1",
                    referred_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get referral by referred_id={referred_id}: {e}")
            return None

    async def get_referral_by_referrer_and_referred(
        self,
        referrer_id: int,
        referred_id: str,
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM referrals WHERE referrer_id = $1 AND referred_id = $2",
                    referrer_id, referred_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(
                f"❌ Failed to get referral by referrer={referrer_id}, "
                f"referred={referred_id}: {e}"
            )
            return None

    async def create_referral(self, referrer_id: int, referred_id: str) -> bool:
        """True только если вставлена новая строка (не конфликт)."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO referrals (referrer_id, referred_id, created_at)
                    VALUES ($1, $2, NOW())
                    ON CONFLICT DO NOTHING
                    RETURNING referred_id
                    """,
                    referrer_id,
                    referred_id,
                )
                if row:
                    logger.info(
                        "✅ Referral created: referrer_id=%s, referred_id=%s",
                        referrer_id,
                        referred_id,
                    )
                    return True
                logger.info(
                    "Referral insert skipped (duplicate): referrer_id=%s, referred_id=%s",
                    referrer_id,
                    referred_id,
                )
                return False
        except Exception as e:
            logger.error(f"❌ Failed to create referral: {e}")
            return False

    async def get_referrer_by_referred(self, referred_id: str) -> Optional[int]:
        try:
            async with self.get_connection() as conn:
                return await conn.fetchval(
                    "SELECT referrer_id FROM referrals WHERE referred_id = $1",
                    referred_id,
                )
        except Exception as e:
            logger.error(f"❌ Failed to get referrer for referred_id={referred_id}: {e}")
            return None

    async def get_referrals_count(self, referrer_id: int) -> int:
        try:
            async with self.get_connection() as conn:
                count = await conn.fetchval(
                    "SELECT COUNT(*) FROM referrals WHERE referrer_id = $1",
                    referrer_id,
                )
                return count or 0
        except Exception as e:
            logger.error(f"❌ Failed to get referrals count for referrer_id={referrer_id}: {e}")
            return 0

    async def get_referral_stats(self, referrer_id: int) -> Dict[str, Any]:
        try:
            async with self.get_connection() as conn:
                total = await self.get_referrals_count(referrer_id)
                monthly = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM referrals
                    WHERE referrer_id = $1
                      AND created_at >= NOW() - INTERVAL '30 days'
                    """,
                    referrer_id,
                ) or 0
                paid = await conn.fetchval(
                    """
                    SELECT COUNT(DISTINCT r.referred_id)
                      FROM referrals r
                      JOIN payments p ON p.user_id::text = r.referred_id
                     WHERE r.referrer_id = $1 AND p.status = 'succeeded'
                    """,
                    referrer_id,
                ) or 0
                bonuses = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                      FROM referrals
                     WHERE referrer_id = $1 AND bonus_granted = TRUE
                    """,
                    referrer_id,
                ) or 0
                recent = await conn.fetch(
                    """
                    SELECT referred_id, created_at
                    FROM referrals
                    WHERE referrer_id = $1
                    ORDER BY created_at DESC
                    LIMIT 5
                    """,
                    referrer_id,
                )
                return {
                    "total": total,
                    "monthly": monthly,
                    "paid": int(paid),
                    "bonuses_given": int(bonuses),
                    "recent": [dict(row) for row in recent],
                }
        except Exception as e:
            logger.error(f"❌ Failed to get referral stats for referrer_id={referrer_id}: {e}")
            return {
                "total": 0,
                "monthly": 0,
                "paid": 0,
                "bonuses_given": 0,
                "recent": [],
            }

    async def get_referrals_list(
        self, referrer_id: int, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Список рефералов с расширенной информацией о пользователе."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT
                        r.referred_id,
                        r.created_at AS referral_date,
                        r.bonus_granted AS referral_bonus_unlocked,
                        u.username,
                        u.first_name,
                        u.last_name,
                        u.created_at AS user_created_at,
                        u.onboarding_complete,
                        u.last_activity,
                        EXISTS (
                          SELECT 1 FROM payments p
                           WHERE p.user_id::text = r.referred_id
                             AND p.status = 'succeeded'
                        ) AS has_paid
                    FROM referrals r
                    LEFT JOIN users u ON u.user_id::text = r.referred_id
                    WHERE r.referrer_id = $1
                    ORDER BY r.created_at DESC
                    LIMIT $2
                    """,
                    referrer_id, limit,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(
                f"❌ Failed to get referrals list for referrer_id={referrer_id}: {e}"
            )
            return []

    async def get_referrer_info(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Информация о реферере данного пользователя."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT r.referrer_id, r.bonus_granted, u.first_name, u.last_name
                    FROM referrals r
                    JOIN users u ON r.referrer_id = u.user_id
                    WHERE r.referred_id = $1
                    """,
                    str(user_id),
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get referrer info: {e}")
            return None

    async def mark_referral_bonus_granted(self, user_id: int) -> bool:
        """Бонус рефереру выдан за первую оплату этого реферала: фиксируем в строке referrals и в users."""
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE referrals
                       SET bonus_granted = TRUE
                     WHERE referred_id = $1
                    """,
                    str(user_id),
                )
                await conn.execute(
                    """
                    UPDATE users
                       SET referral_bonus_granted = TRUE
                     WHERE user_id = $1
                    """,
                    user_id,
                )
                logger.info(
                    f"✅ Referral bonus marked as granted for referred user_id={user_id}"
                )
                return True
        except Exception as e:
            logger.error(f"❌ Failed to mark referral bonus for user {user_id}: {e}")
            return False

    async def get_ref_key_name(self, ref_key: str) -> Optional[str]:
        """Имя кампании по ref_key из ref_keys; None если ключа нет."""
        key = (ref_key or "").strip()
        if key.startswith("ref_"):
            key = key[4:]
        if not key:
            return None
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT name FROM ref_keys WHERE ref_key = $1",
                    key,
                )
                return row["name"] if row else None
        except Exception as e:
            logger.error("❌ Failed to get ref_keys name for %s: %s", ref_key, e)
            return None

    async def resolve_touch_display_name(
        self, touch_key: Optional[str], ref_key: Optional[str] = None
    ) -> Optional[str]:
        if ref_key:
            name = await self.get_ref_key_name(ref_key)
            if name:
                return name
        from bot.services.attribution_touch import ref_key_for_lookup

        lookup = ref_key_for_lookup(touch_key)
        if lookup:
            name = await self.get_ref_key_name(lookup)
            if name:
                return name
        if touch_key and hasattr(self, "get_touch_key_label_name"):
            return await self.get_touch_key_label_name(touch_key)
        return None

    async def _touch_ref_name(
        self, touch_key: Optional[str], ref_key: Optional[str] = None
    ) -> Optional[str]:
        return await self.resolve_touch_display_name(touch_key, ref_key)

    async def get_first_start_source_display(self, user_id: int) -> str:
        """Первое маркетинговое касание: users.first_touch_*, иначе attribution, иначе /start с payload."""
        from bot.services.attribution_touch import format_touch_key_plain

        try:
            async with self.get_connection() as conn:
                user = await conn.fetchrow(
                    """
                    SELECT first_touch_key, first_touch_kind
                    FROM users WHERE user_id = $1
                    """,
                    user_id,
                )
            if user and user["first_touch_key"]:
                ref_name = await self.resolve_touch_display_name(
                    user["first_touch_key"]
                )
                return format_touch_key_plain(user["first_touch_key"], ref_name)

            if hasattr(self, "get_first_meaningful_marketing_touch"):
                touch = await self.get_first_meaningful_marketing_touch(user_id)
                if touch:
                    ref_name = await self.resolve_touch_display_name(
                        touch.get("touch_key"), touch.get("ref_key")
                    )
                    return format_touch_key_plain(touch["touch_key"], ref_name)

            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT data->>'text' AS text
                    FROM interaction_logs
                    WHERE user_id = $1
                      AND event_category = 'message'
                      AND COALESCE(data->>'text', '') ILIKE '/start%'
                    ORDER BY created_at ASC
                    """,
                    user_id,
                )
            if not row or not row["text"]:
                return "—"

            raw = (row["text"] or "").strip()
            mo = _START_CMD_RE.match(raw)
            if not mo:
                return raw[:300] if raw else "—"

            rest = (mo.group(1) or "").strip()
            if not rest:
                return "без параметров"

            first_token = rest.split()[0]
            if first_token.startswith("ref_"):
                ref_name = await self.resolve_touch_display_name(first_token)
                return format_touch_key_plain(first_token, ref_name)

            if first_token.startswith("gift_"):
                return first_token[:200]

            ref_name = await self.resolve_touch_display_name(first_token)
            if ref_name:
                return format_touch_key_plain(first_token, ref_name)

            return rest[:500]
        except Exception as e:
            logger.error(
                "❌ Failed to get first arrival source for user %s: %s", user_id, e
            )
            return "—"

    async def get_last_referral_source(self, user_id: int) -> Optional[str]:
        """Достаёт из interaction_logs последний `/start ref_<key>` пользователя."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT data->>'text' as text
                    FROM interaction_logs
                    WHERE user_id = $1
                      AND event_category = 'message'
                      AND data->>'text' LIKE '/start ref_%'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    user_id,
                )
                if not row or not row["text"]:
                    return None

                match = re.search(r"ref_([a-zA-Z0-9]+)", row["text"])
                if not match:
                    return None
                ref_key = match.group(1)

            name = await self.get_ref_key_name(ref_key)
            return name if name else ref_key
        except Exception as e:
            logger.error(f"❌ Failed to get referral source for user {user_id}: {e}")
            return None

    async def ref_key_exists(self, ref_key: str) -> bool:
        key = (ref_key or "").strip()
        if not key:
            return False
        try:
            async with self.get_connection() as conn:
                val = await conn.fetchval(
                    "SELECT 1 FROM ref_keys WHERE ref_key = $1",
                    key,
                )
                return bool(val)
        except Exception as e:
            logger.error("ref_key_exists %s: %s", ref_key, e)
            return False

    async def should_queue_ref_key_for_naming(self, ref_key: str) -> bool:
        """Нужен ли псевдоним в ref_keys (кампания, не user-referral и не мусор)."""
        from bot.services.ref_key_registry import is_garbage_ref_key

        key = (ref_key or "").strip()
        if not key or is_garbage_ref_key(key):
            return False
        if await self.ref_key_exists(key):
            return False
        if key.isdigit():
            try:
                uid = int(key)
                if uid > 0 and await self.get_user(uid):
                    return False
            except ValueError:
                pass
        return True

    async def upsert_ref_key_pending(
        self, ref_key: str, sample_touch_key: Optional[str] = None
    ) -> bool:
        """Возвращает True, если ключ впервые попал в очередь."""
        key = (ref_key or "").strip()
        if not key:
            return False
        try:
            async with self.get_connection() as conn:
                exists = await conn.fetchval(
                    "SELECT 1 FROM ref_key_pending WHERE ref_key = $1",
                    key,
                )
                if exists:
                    await conn.execute(
                        """
                        UPDATE ref_key_pending
                        SET last_seen_at = NOW(),
                            touch_count = touch_count + 1,
                            sample_touch_key = COALESCE(
                                sample_touch_key, $2
                            )
                        WHERE ref_key = $1
                        """,
                        key,
                        sample_touch_key,
                    )
                    return False
                await conn.execute(
                    """
                    INSERT INTO ref_key_pending (
                        ref_key, sample_touch_key, first_seen_at, last_seen_at, touch_count
                    ) VALUES ($1, $2, NOW(), NOW(), 1)
                    """,
                    key,
                    sample_touch_key,
                )
                return True
        except Exception as e:
            logger.error("upsert_ref_key_pending %s: %s", ref_key, e)
            return False

    async def list_ref_key_pending(
        self, *, include_dismissed: bool = False, limit: int = 50
    ) -> List[Dict[str, Any]]:
        try:
            dismissed_clause = "" if include_dismissed else "AND dismissed_at IS NULL"
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT ref_key, sample_touch_key, first_seen_at, last_seen_at,
                           touch_count, admin_notified_at, dismissed_at, resolved_at
                    FROM ref_key_pending
                    WHERE resolved_at IS NULL
                      {dismissed_clause}
                    ORDER BY first_seen_at DESC
                    LIMIT $1
                    """,
                    max(1, int(limit)),
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_ref_key_pending: %s", e)
            return []

    async def list_ref_key_pending_for_notify(self, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT ref_key, sample_touch_key, touch_count, first_seen_at
                    FROM ref_key_pending
                    WHERE admin_notified_at IS NULL
                      AND dismissed_at IS NULL
                      AND resolved_at IS NULL
                    ORDER BY first_seen_at ASC
                    LIMIT $1
                    """,
                    max(1, int(limit)),
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_ref_key_pending_for_notify: %s", e)
            return []

    async def mark_ref_key_pending_notified(self, ref_key: str) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE ref_key_pending
                    SET admin_notified_at = COALESCE(admin_notified_at, NOW())
                    WHERE ref_key = $1
                    """,
                    ref_key,
                )
        except Exception as e:
            logger.error("mark_ref_key_pending_notified %s: %s", ref_key, e)

    async def dismiss_ref_key_pending(self, ref_key: str) -> bool:
        try:
            async with self.get_connection() as conn:
                result = await conn.execute(
                    """
                    UPDATE ref_key_pending
                    SET dismissed_at = COALESCE(dismissed_at, NOW())
                    WHERE ref_key = $1 AND resolved_at IS NULL
                    """,
                    ref_key,
                )
                return result.endswith("1")
        except Exception as e:
            logger.error("dismiss_ref_key_pending %s: %s", ref_key, e)
            return False

    async def resolve_ref_key_pending(self, ref_key: str) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE ref_key_pending
                    SET resolved_at = COALESCE(resolved_at, NOW())
                    WHERE ref_key = $1
                    """,
                    ref_key,
                )
        except Exception as e:
            logger.error("resolve_ref_key_pending %s: %s", ref_key, e)

    async def create_ref_key_entry(
        self,
        ref_key: str,
        name: str,
        *,
        type_label: Optional[str] = None,
        description: Optional[str] = None,
    ) -> bool:
        key = (ref_key or "").strip()
        label = (name or "").strip()
        if not key or not label:
            return False
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO ref_keys (ref_key, name, type, description, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, NOW(), NOW())
                    ON CONFLICT (ref_key) DO UPDATE SET
                        name = EXCLUDED.name,
                        type = COALESCE(EXCLUDED.type, ref_keys.type),
                        description = COALESCE(EXCLUDED.description, ref_keys.description),
                        updated_at = NOW()
                    """,
                    key,
                    label,
                    (type_label or "").strip() or None,
                    (description or "").strip() or None,
                )
            await self.resolve_ref_key_pending(key)
            return True
        except Exception as e:
            logger.error("create_ref_key_entry %s: %s", ref_key, e)
            return False

    async def list_ref_key_types(self) -> List[str]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT type FROM ref_keys
                    WHERE type IS NOT NULL AND TRIM(type) <> ''
                    ORDER BY type
                    """
                )
                return [str(r["type"]) for r in rows if r.get("type")]
        except Exception as e:
            logger.error("list_ref_key_types: %s", e)
            return []
