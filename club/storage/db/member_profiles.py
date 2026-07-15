"""
Mixin: профиль участника клуба (`member_profiles`) для member-агента.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ONBOARDING_STARTED = "started"
_ONBOARDING_ACTIVE = "active"
_MAX_TOPICS = 8
_MAX_MATERIALS = 15
_TOPIC_MAX_LEN = 120


def _json_list(val: Any) -> List[Any]:
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


class MemberProfilesMixin:

    async def get_member_profile(self, user_id: int) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM member_profiles WHERE user_id = $1",
                    user_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_member_profile uid=%s: %s", user_id, e)
            return None

    async def ensure_member_profile(self, user_id: int) -> Dict[str, Any]:
        existing = await self.get_member_profile(user_id)
        if existing:
            return existing
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO member_profiles (user_id)
                    VALUES ($1)
                    ON CONFLICT (user_id) DO NOTHING
                    RETURNING *
                    """,
                    user_id,
                )
                if row:
                    return dict(row)
                row = await conn.fetchrow(
                    "SELECT * FROM member_profiles WHERE user_id = $1",
                    user_id,
                )
                return dict(row) if row else {"user_id": user_id}
        except Exception as e:
            logger.error("ensure_member_profile uid=%s: %s", user_id, e)
            return {"user_id": user_id}

    async def log_member_profile_event(
        self,
        user_id: int,
        event_type: str,
        *,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO member_profile_events (user_id, event_type, meta)
                    VALUES ($1, $2, $3::jsonb)
                    """,
                    user_id,
                    (event_type or "").strip()[:64],
                    json.dumps(meta or {}, ensure_ascii=False),
                )
        except Exception as e:
            logger.warning("log_member_profile_event uid=%s: %s", user_id, e)

    async def on_member_subscription_started(
        self,
        user_id: int,
        *,
        license_expires_at: datetime,
        is_first_join: bool,
    ) -> None:
        """Новый период участия после оплаты (не продление с активной лицензией)."""
        await self.ensure_member_profile(user_id)
        try:
            async with self.get_connection() as conn:
                if is_first_join:
                    await conn.execute(
                        """
                        UPDATE member_profiles
                        SET joined_at = COALESCE(joined_at, NOW()),
                            license_expires_at = $2,
                            onboarding_stage = $3,
                            renewal_state = 'none',
                            updated_at = NOW()
                        WHERE user_id = $1
                        """,
                        user_id,
                        license_expires_at,
                        _ONBOARDING_STARTED,
                    )
                    await self.log_member_profile_event(
                        user_id,
                        "subscription_started",
                        meta={"expires_at": license_expires_at.isoformat()},
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE member_profiles
                        SET license_expires_at = $2,
                            updated_at = NOW()
                        WHERE user_id = $1
                        """,
                        user_id,
                        license_expires_at,
                    )
                    await self.log_member_profile_event(
                        user_id,
                        "subscription_renewed",
                        meta={"expires_at": license_expires_at.isoformat()},
                    )
        except Exception as e:
            logger.error("on_member_subscription_started uid=%s: %s", user_id, e)

    async def sync_member_license_expires(
        self, user_id: int, license_expires_at: datetime
    ) -> None:
        await self.ensure_member_profile(user_id)
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE member_profiles
                    SET license_expires_at = $2, updated_at = NOW()
                    WHERE user_id = $1
                    """,
                    user_id,
                    license_expires_at,
                )
        except Exception as e:
            logger.error("sync_member_license_expires uid=%s: %s", user_id, e)

    async def touch_member_dm(
        self,
        user_id: int,
        *,
        user_message: Optional[str] = None,
    ) -> None:
        await self.ensure_member_profile(user_id)
        topic_snippet = (user_message or "").strip()[:_TOPIC_MAX_LEN]
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT topics_json, onboarding_stage FROM member_profiles WHERE user_id = $1",
                    user_id,
                )
                topics = _json_list(row["topics_json"]) if row else []
                if topic_snippet and topic_snippet not in topics:
                    topics = [topic_snippet] + topics
                    topics = topics[:_MAX_TOPICS]

                stage = (row["onboarding_stage"] if row else None) or "not_started"
                new_stage = stage
                if stage == _ONBOARDING_STARTED:
                    new_stage = _ONBOARDING_ACTIVE

                await conn.execute(
                    """
                    UPDATE member_profiles
                    SET last_dm_at = NOW(),
                        topics_json = $2::jsonb,
                        onboarding_stage = $3,
                        updated_at = NOW()
                    WHERE user_id = $1
                    """,
                    user_id,
                    json.dumps(topics, ensure_ascii=False),
                    new_stage,
                )
        except Exception as e:
            logger.error("touch_member_dm uid=%s: %s", user_id, e)

    async def set_member_stated_goals(self, user_id: int, goals_text: str) -> None:
        text = (goals_text or "").strip()[:2000]
        if not text:
            return
        await self.ensure_member_profile(user_id)
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE member_profiles
                    SET stated_goals = $2, updated_at = NOW()
                    WHERE user_id = $1
                    """,
                    user_id,
                    text,
                )
            await self.log_member_profile_event(
                user_id, "stated_goals_updated", meta={"len": len(text), "mode": "replace"}
            )
        except Exception as e:
            logger.error("set_member_stated_goals uid=%s: %s", user_id, e)

    async def append_member_stated_goals_fragment(
        self,
        user_id: int,
        fragment: str,
        *,
        source: str = "llm_extract",
    ) -> bool:
        """Дополняет stated_goals фрагментом (merge в транзакции, без перезаписи)."""
        from bot.services.member_goals_merge import merge_stated_goals_fragment

        frag = (fragment or "").strip()
        if not frag:
            return False
        await self.ensure_member_profile(user_id)
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT stated_goals FROM member_profiles WHERE user_id = $1",
                    user_id,
                )
                current = (row["stated_goals"] or "") if row else ""
                merged, changed = merge_stated_goals_fragment(current, frag)
                if not changed:
                    return False
                await conn.execute(
                    """
                    UPDATE member_profiles
                    SET stated_goals = $2, updated_at = NOW()
                    WHERE user_id = $1
                    """,
                    user_id,
                    merged,
                )
            await self.log_member_profile_event(
                user_id,
                "stated_goals_updated",
                meta={
                    "len": len(merged),
                    "mode": "append",
                    "source": source[:32],
                    "fragment_len": len(frag),
                },
            )
            return True
        except Exception as e:
            logger.error("append_member_stated_goals_fragment uid=%s: %s", user_id, e)
            return False

    async def record_member_materials_sent(
        self, user_id: int, links: List[str]
    ) -> None:
        clean = [u.strip() for u in links if u and str(u).strip()]
        if not clean:
            return
        await self.ensure_member_profile(user_id)
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT materials_sent_json FROM member_profiles WHERE user_id = $1",
                    user_id,
                )
                items = _json_list(row["materials_sent_json"]) if row else []
                now_iso = datetime.now().isoformat()
                for link in clean:
                    entry = {"link": link, "at": now_iso}
                    items = [entry] + [
                        x for x in items if isinstance(x, dict) and x.get("link") != link
                    ]
                items = items[:_MAX_MATERIALS]
                await conn.execute(
                    """
                    UPDATE member_profiles
                    SET materials_sent_json = $2::jsonb, updated_at = NOW()
                    WHERE user_id = $1
                    """,
                    user_id,
                    json.dumps(items, ensure_ascii=False),
                )
        except Exception as e:
            logger.error("record_member_materials_sent uid=%s: %s", user_id, e)

    async def record_proactive_sent(
        self,
        user_id: int,
        *,
        goal: str = "",
        reason: str = "",
    ) -> None:
        await self.ensure_member_profile(user_id)
        cooldown = datetime.now() + timedelta(hours=24)
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE member_profiles
                    SET proactive_cooldown_until = $2,
                        updated_at = NOW()
                    WHERE user_id = $1
                    """,
                    user_id,
                    cooldown,
                )
            await self.log_member_profile_event(
                user_id,
                "proactive_sent",
                meta={"goal": goal, "reason": reason[:500]},
            )
        except Exception as e:
            logger.error("record_proactive_sent uid=%s: %s", user_id, e)

    async def proactive_slot_available(
        self,
        user_id: int,
        *,
        profile: Optional[Dict[str, Any]] = None,
    ) -> bool:
        prof = profile or await self.get_member_profile(user_id)
        if not prof:
            return True
        until = prof.get("proactive_cooldown_until")
        if until is None:
            return True
        if isinstance(until, datetime):
            return until <= datetime.now(until.tzinfo or __import__("datetime").timezone.utc)
        return True

    async def list_proactive_candidates(self, *, limit: int = 60) -> List[Dict[str, Any]]:
        """Участники с активной лицензией для проактива (приоритет — давно без DM)."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT
                        l.user_id,
                        u.first_name,
                        l.expires_at,
                        GREATEST(
                            0,
                            (l.expires_at::date - CURRENT_DATE)
                        )::int AS days_to_expiry,
                        mp.last_dm_at,
                        mp.onboarding_stage,
                        mp.proactive_ignored_streak
                    FROM license l
                    JOIN users u ON u.user_id = l.user_id
                    LEFT JOIN member_profiles mp ON mp.user_id = l.user_id
                    WHERE l.status = 'active'
                      AND l.expires_at > NOW()
                      AND COALESCE(u.is_active, TRUE)
                    ORDER BY
                        mp.last_dm_at NULLS FIRST,
                        l.expires_at ASC
                    LIMIT $1
                    """,
                    limit,
                )
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_proactive_candidates: %s", e)
            return []

    async def set_member_renewal_state(self, user_id: int, state: str) -> None:
        await self.ensure_member_profile(user_id)
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE member_profiles
                    SET renewal_state = $2, updated_at = NOW()
                    WHERE user_id = $1
                    """,
                    user_id,
                    (state or "none").strip()[:32],
                )
        except Exception as e:
            logger.error("set_member_renewal_state uid=%s: %s", user_id, e)

    async def touch_member_group_activity(self, user_id: int) -> None:
        await self.ensure_member_profile(user_id)
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE member_profiles
                    SET last_group_activity_at = NOW(), updated_at = NOW()
                    WHERE user_id = $1
                    """,
                    user_id,
                )
        except Exception as e:
            logger.error("touch_member_group_activity uid=%s: %s", user_id, e)
