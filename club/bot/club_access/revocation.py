"""Единая точка отзыва доступа в клуб.

Зачем. Исключением из группы занимались два независимых механизма со своими
условиями, своими записями в журнал и своим (не)уведомлением человека:

* **плановый** — `subscription_reminder`, в 9 МСК на следующий день после
  окончания доступа, reason `bonus_expired`. Основной: пишет человеку
  прощальное письмо, объясняет, как вернуться;
* **ночной аудит** — `club_group`, в 21:00 UTC, reason `nightly_audit`.
  Подстраховка: ходит по участникам чата и убирает тех, у кого доступа нет
  дольше CLUB_GROUP_EXPIRED_LICENSE_GRACE_DAYS.

Они разъезжались. На 24 октября 2026 **десять человек были исключены обоими**
— то есть дважды, с двумя записями в журнале. А отсрочку по просьбе на Доске
добрых дел знал только плановый: аварийный отменял её, потому что его порог
(3 дня) короче отсрочки (7 дней).

Теперь решение и само действие живут здесь, а оба механизма только зовут
`revoke()` со своим `reason`. Логика «кого держим», журнал, снятие лицензии и
уведомление — в одном месте.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Optional

from config import config

logger = logging.getLogger(__name__)

#: Плановый механизм. Основной: он и пишет человеку.
SOURCE_SCHEDULED = "subscription_reminder"
REASON_SCHEDULED = "bonus_expired"

#: Ночной аудит. Подстраховка за плановым.
SOURCE_AUDIT = "club_group"
REASON_AUDIT = "nightly_audit"

#: Сколько часов после исключения считаем «только что убрали».
#: Защита от двойного кика двумя механизмами подряд.
RECENT_EXCLUSION_HOURS = 48


class RevokeOutcome(str, Enum):
    REMOVED = "removed"
    HELD_BY_WISH = "held_by_wish"
    SKIPPED_ACTIVE_LICENSE = "skipped_active_license"
    SKIPPED_RECENTLY_REMOVED = "skipped_recently_removed"
    SKIPPED_ADMIN = "skipped_admin"
    SKIPPED_NOT_CONFIGURED = "skipped_not_configured"
    FAILED = "failed"


@dataclass
class RevokeDecision:
    outcome: RevokeOutcome
    user_id: int
    reason: str
    source: str
    wish: Optional[Dict[str, Any]] = None
    detail: str = ""

    @property
    def removed(self) -> bool:
        return self.outcome is RevokeOutcome.REMOVED

    @property
    def held(self) -> bool:
        return self.outcome is RevokeOutcome.HELD_BY_WISH


class ClubAccessRevoker:
    def __init__(self, user_storage, bot):
        self.user_storage = user_storage
        self.bot = bot

    # ── Решение ──────────────────────────────────────────────────────────
    async def decide(
        self,
        user_id: int,
        *,
        reason: str,
        source: str,
        expires_at: Optional[datetime] = None,
    ) -> RevokeDecision:
        """Можно ли убирать человека. Ничего не меняет — только отвечает."""
        uid = int(user_id)

        def _d(outcome: RevokeOutcome, **kw) -> RevokeDecision:
            return RevokeDecision(
                outcome=outcome, user_id=uid, reason=reason, source=source, **kw
            )

        club_gid = int(getattr(config, "CLUB_GROUP_ID", 0) or 0)
        if not club_gid:
            return _d(RevokeOutcome.SKIPPED_NOT_CONFIGURED, detail="CLUB_GROUP_ID=0")

        try:
            if await self.user_storage.is_telegram_admin_id(uid):
                return _d(RevokeOutcome.SKIPPED_ADMIN)
        except Exception as e:
            logger.debug("revoke admin check uid=%s: %s", uid, e)

        # Доступ мог появиться между решением и действием — например, ему
        # как раз оплатили продление с Доски.
        try:
            if await self.user_storage.user_has_active_license(uid):
                return _d(RevokeOutcome.SKIPPED_ACTIVE_LICENSE)
        except Exception as e:
            logger.debug("revoke license check uid=%s: %s", uid, e)

        # Не убираем дважды. Раньше плановый и аудит кикали одного и того же
        # человека по очереди — десять таких случаев на 24 октября.
        if await self._removed_recently(uid):
            return _d(RevokeOutcome.SKIPPED_RECENTLY_REMOVED)

        from bot.services.wish_board_kick_hold import wish_holding_removal

        wish = await wish_holding_removal(
            self.user_storage, uid, expires_at=expires_at
        )
        if wish:
            return _d(RevokeOutcome.HELD_BY_WISH, wish=wish)

        return _d(RevokeOutcome.REMOVED)

    async def _removed_recently(self, user_id: int) -> bool:
        try:
            async with self.user_storage.get_connection() as conn:
                val = await conn.fetchval(
                    """
                    SELECT 1 FROM club_member_exclusions
                    WHERE user_id = $1 AND excluded_at >= $2
                    LIMIT 1
                    """,
                    int(user_id),
                    datetime.now(timezone.utc)
                    - timedelta(hours=RECENT_EXCLUSION_HOURS),
                )
            return bool(val)
        except Exception as e:
            logger.warning("revoke recent-exclusion check uid=%s: %s", user_id, e)
            return False

    # ── Действие ─────────────────────────────────────────────────────────
    async def revoke(
        self,
        user_id: int,
        *,
        reason: str,
        source: str,
        expires_at: Optional[datetime] = None,
        decision: Optional[RevokeDecision] = None,
    ) -> RevokeDecision:
        """Убрать из группы: кик, запись в журнал, снятие лицензии.

        Уведомление человека остаётся за вызывающим: у планового механизма
        своё прощальное письмо, у аудита — отчёт админам.
        """
        d = decision or await self.decide(
            user_id, reason=reason, source=source, expires_at=expires_at
        )
        if d.outcome is not RevokeOutcome.REMOVED:
            return d

        uid = int(user_id)
        club_gid = int(getattr(config, "CLUB_GROUP_ID", 0) or 0)
        try:
            await self.bot.ban_chat_member(chat_id=club_gid, user_id=uid)
            await self.bot.unban_chat_member(chat_id=club_gid, user_id=uid)
        except Exception as e:
            logger.error("revoke kick uid=%s: %s", uid, e)
            return RevokeDecision(
                outcome=RevokeOutcome.FAILED,
                user_id=uid,
                reason=reason,
                source=source,
                detail=str(e)[:200],
            )

        logger.info("🚪 Исключён из клуба uid=%s (%s/%s)", uid, reason, source)
        for call, label in (
            (
                self.user_storage.record_club_member_exclusion(
                    uid, reason=reason, source=source
                ),
                "журнал",
            ),
            (self.user_storage.mark_license_expired(uid), "лицензия"),
            (self.user_storage.delete_club_member_cache(uid), "кэш группы"),
        ):
            try:
                await call
            except Exception as e:
                logger.warning("revoke %s uid=%s: %s", label, uid, e)
        return d
