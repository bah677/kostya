"""Псевдонимы touch_key (колбэки / promo) — аналог ref_keys для диплинков."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_GENERIC_TOUCH_KEYS = frozenset({"payment_start"})


class TouchKeyLabelsMixin:

    def is_generic_touch_key(self, touch_key: str) -> bool:
        return (touch_key or "").strip() in _GENERIC_TOUCH_KEYS

    async def touch_key_label_exists(self, touch_key: str) -> bool:
        key = (touch_key or "").strip()
        if not key:
            return False
        try:
            async with self.get_connection() as conn:
                val = await conn.fetchval(
                    "SELECT 1 FROM touch_key_labels WHERE touch_key = $1",
                    key,
                )
                return bool(val)
        except Exception as e:
            logger.error("touch_key_label_exists %s: %s", touch_key, e)
            return False

    async def get_touch_key_label_name(self, touch_key: str) -> Optional[str]:
        key = (touch_key or "").strip()
        if not key:
            return None
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchval(
                    "SELECT name FROM touch_key_labels WHERE touch_key = $1",
                    key,
                )
                return row
        except Exception as e:
            logger.error("get_touch_key_label_name %s: %s", touch_key, e)
            return None

    async def should_queue_touch_key_for_naming(
        self, touch_key: str, ref_key: Optional[str] = None
    ) -> bool:
        from bot.services.attribution_touch import is_checkout_step_touch_key

        key = (touch_key or "").strip()
        if not key or is_checkout_step_touch_key(key):
            return False
        if self.is_generic_touch_key(key):
            return False
        if (ref_key or "").strip():
            return False
        if key.startswith("ref_"):
            return False
        if await self.touch_key_label_exists(key):
            return False
        return True

    async def upsert_touch_key_pending(self, touch_key: str) -> bool:
        key = (touch_key or "").strip()
        if not key:
            return False
        try:
            async with self.get_connection() as conn:
                exists = await conn.fetchval(
                    "SELECT 1 FROM touch_key_pending WHERE touch_key = $1",
                    key,
                )
                if exists:
                    await conn.execute(
                        """
                        UPDATE touch_key_pending
                        SET last_seen_at = NOW(), touch_count = touch_count + 1
                        WHERE touch_key = $1
                        """,
                        key,
                    )
                    return False
                await conn.execute(
                    """
                    INSERT INTO touch_key_pending (
                        touch_key, first_seen_at, last_seen_at, touch_count
                    ) VALUES ($1, NOW(), NOW(), 1)
                    """,
                    key,
                )
                return True
        except Exception as e:
            logger.error("upsert_touch_key_pending %s: %s", touch_key, e)
            return False

    async def list_touch_key_pending(
        self, *, include_dismissed: bool = False, limit: int = 50
    ) -> List[Dict[str, Any]]:
        try:
            dismissed_clause = "" if include_dismissed else "AND dismissed_at IS NULL"
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT id, touch_key, first_seen_at, last_seen_at, touch_count,
                           admin_notified_at, dismissed_at, resolved_at
                    FROM touch_key_pending
                    WHERE resolved_at IS NULL
                      {dismissed_clause}
                    ORDER BY first_seen_at DESC
                    LIMIT $1
                    """,
                    max(1, int(limit)),
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_touch_key_pending: %s", e)
            return []

    async def list_touch_key_pending_for_notify(
        self, limit: int = 20
    ) -> List[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, touch_key, touch_count, first_seen_at
                    FROM touch_key_pending
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
            logger.error("list_touch_key_pending_for_notify: %s", e)
            return []

    async def get_touch_key_pending_row(
        self, pending_id: int
    ) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM touch_key_pending WHERE id = $1",
                    int(pending_id),
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_touch_key_pending_row %s: %s", pending_id, e)
            return None

    async def mark_touch_key_pending_notified(self, pending_id: int) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE touch_key_pending
                    SET admin_notified_at = COALESCE(admin_notified_at, NOW())
                    WHERE id = $1
                    """,
                    int(pending_id),
                )
        except Exception as e:
            logger.error("mark_touch_key_pending_notified %s: %s", pending_id, e)

    async def dismiss_touch_key_pending(self, pending_id: int) -> bool:
        try:
            async with self.get_connection() as conn:
                result = await conn.execute(
                    """
                    UPDATE touch_key_pending
                    SET dismissed_at = COALESCE(dismissed_at, NOW())
                    WHERE id = $1 AND resolved_at IS NULL
                    """,
                    int(pending_id),
                )
                return result.endswith("1")
        except Exception as e:
            logger.error("dismiss_touch_key_pending %s: %s", pending_id, e)
            return False

    async def resolve_touch_key_pending(self, touch_key: str) -> None:
        key = (touch_key or "").strip()
        if not key:
            return
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE touch_key_pending
                    SET resolved_at = COALESCE(resolved_at, NOW())
                    WHERE touch_key = $1
                    """,
                    key,
                )
        except Exception as e:
            logger.error("resolve_touch_key_pending %s: %s", touch_key, e)

    async def create_touch_key_label_entry(
        self,
        touch_key: str,
        name: str,
        *,
        type_label: Optional[str] = None,
        description: Optional[str] = None,
    ) -> bool:
        key = (touch_key or "").strip()
        label = (name or "").strip()
        if not key or not label:
            return False
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO touch_key_labels (
                        touch_key, name, type, description, created_at, updated_at
                    ) VALUES ($1, $2, $3, $4, NOW(), NOW())
                    ON CONFLICT (touch_key) DO UPDATE SET
                        name = EXCLUDED.name,
                        type = COALESCE(EXCLUDED.type, touch_key_labels.type),
                        description = COALESCE(
                            EXCLUDED.description, touch_key_labels.description
                        ),
                        updated_at = NOW()
                    """,
                    key,
                    label,
                    (type_label or "").strip() or None,
                    (description or "").strip() or None,
                )
            await self.resolve_touch_key_pending(key)
            return True
        except Exception as e:
            logger.error("create_touch_key_label_entry %s: %s", touch_key, e)
            return False

    async def list_touch_key_label_types(self) -> List[str]:
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT type FROM touch_key_labels
                    WHERE type IS NOT NULL AND TRIM(type) <> ''
                    UNION
                    SELECT DISTINCT type FROM ref_keys
                    WHERE type IS NOT NULL AND TRIM(type) <> ''
                    ORDER BY 1
                    """
                )
                return [str(r["type"]) for r in rows if r.get("type")]
        except Exception as e:
            logger.error("list_touch_key_label_types: %s", e)
            return await self.list_ref_key_types() if hasattr(self, "list_ref_key_types") else []
