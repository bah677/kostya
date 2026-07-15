"""
Mixin: лицензии (`license`).
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from storage.license_types import (
    LICENSE_TYPE_ADMIN_SUBSCRIPTION,
    LICENSE_TYPE_BONUS,
    LICENSE_TYPE_BONUS_EXTENSION,
    REMINDER_ELIGIBLE_LICENSE_TYPES,
)

logger = logging.getLogger(__name__)


def _to_utc_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Единый стиль дат для asyncpg (нет смешения naive/aware при записи истории)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Для колонок TIMESTAMP WITHOUT TIME ZONE (например license.expires_at)."""
    aw = _to_utc_aware(dt)
    if aw is None:
        return None
    return aw.replace(tzinfo=None)


class LicensesMixin:

    async def get_user_license_info(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Возвращает активную лицензию (с amount/created_at из payments).

        Если срок истёк — переводит в 'expired' и возвращает None.
        """
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        l.*,
                        p.amount,
                        p.created_at as payment_date
                    FROM license l
                    LEFT JOIN payments p ON l.payment_id = p.id
                    WHERE l.user_id = $1 AND l.status = 'active'
                    ORDER BY l.created_at DESC
                    LIMIT 1
                    """,
                    user_id,
                )

                if not row:
                    return None

                license_info = dict(row)
                expires_at = license_info.get("expires_at")
                if expires_at and expires_at < datetime.now():
                    did = await conn.fetchval(
                        """
                        UPDATE license
                           SET status = 'expired', updated_at = NOW()
                         WHERE user_id = $1 AND status = 'active'
                        RETURNING 1
                        """,
                        user_id,
                    )
                    if did is not None:
                        await self.append_license_history(
                            user_id=user_id,
                            previous_expires_at=expires_at,
                            new_expires_at=expires_at,
                            source="expired_detected_on_read",
                            meta={"status_change": "active_to_expired"},
                        )
                    return None

                return license_info
        except Exception as e:
            logger.error(f"❌ Failed to get license info for user_id={user_id}: {e}")
            return None

    async def get_expiring_licenses(self, days_before: int = 3) -> List[Dict[str, Any]]:
        """Активные лицензии, истекающие в ближайшие days_before дней."""
        try:
            async with self.get_connection() as conn:
                expiration_date = datetime.now() + timedelta(days=days_before)
                rows = await conn.fetch(
                    """
                    SELECT
                        l.*,
                        u.username,
                        u.first_name,
                        u.last_name,
                        p.amount,
                        p.payment_type
                    FROM license l
                    JOIN users u ON l.user_id = u.user_id
                    LEFT JOIN payments p ON l.payment_id = p.id
                    WHERE l.status = 'active'
                      AND l.expires_at BETWEEN NOW() AND $1
                    ORDER BY l.expires_at ASC
                    """,
                    expiration_date,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get expiring licenses: {e}")
            return []

    async def get_user_active_license(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Активная (не истекшая) лицензия пользователя."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM license
                    WHERE user_id = $1 AND status = 'active' AND expires_at > NOW()
                    ORDER BY expires_at DESC
                    LIMIT 1
                    """,
                    user_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get active license for user {user_id}: {e}")
            return None

    async def club_nightly_audit_should_remove_member(
        self, user_id: int, grace_days: int = 3
    ) -> bool:
        """
        Ночной аудит закрытой группы: удалять из чата только если нет действующей подписки
        и последняя известная дата окончания (MAX(expires_at) в license) раньше,
        чем (сейчас − grace_days). При grace_days=0 поведение как раньше без отсрочки.

        Нет ни одной строки license — как раньше: удалять (нет оснований держать в чате).
        """
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        EXISTS(
                            SELECT 1 FROM license
                            WHERE user_id = $1
                              AND status = 'active'
                              AND expires_at > NOW()
                        ) AS has_active,
                        (SELECT MAX(expires_at) FROM license WHERE user_id = $1) AS last_expires
                    """,
                    user_id,
                )
            if not row:
                return False
            if row["has_active"]:
                return False
            last_expires = row["last_expires"]
            if last_expires is None:
                return True
            last_utc = _to_utc_aware(last_expires)
            if last_utc is None:
                return True
            cutoff = datetime.now(timezone.utc) - timedelta(days=grace_days)
            return last_utc < cutoff
        except Exception as e:
            logger.error(
                "❌ club_nightly_audit_should_remove_member user=%s: %s",
                user_id,
                e,
            )
            return False

    async def list_user_ids_with_active_license(self) -> List[int]:
        """Все user_id с неистёкшей активной лицензией (включая бонусные продления)."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT user_id FROM license
                    WHERE status = 'active' AND expires_at > NOW()
                    ORDER BY user_id
                    """
                )
                return [r["user_id"] for r in rows]
        except Exception as e:
            logger.error(f"❌ Failed to list active license user ids: {e}")
            return []

    async def append_license_history(
        self,
        user_id: int,
        previous_expires_at: Optional[Any],
        new_expires_at: Any,
        source: str,
        order_id: Optional[int] = None,
        payment_id: Optional[int] = None,
        referred_user_id: Optional[int] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            import json as _json
            async with self.get_connection() as conn:
                if meta is not None:
                    await conn.execute(
                        """
                        INSERT INTO license_history
                            (user_id, previous_expires_at, new_expires_at, source,
                             order_id, payment_id, referred_user_id, meta)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                        """,
                        user_id,
                        previous_expires_at,
                        new_expires_at,
                        source,
                        order_id,
                        payment_id,
                        referred_user_id,
                        _json.dumps(meta),
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO license_history
                            (user_id, previous_expires_at, new_expires_at, source,
                             order_id, payment_id, referred_user_id)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        """,
                        user_id,
                        previous_expires_at,
                        new_expires_at,
                        source,
                        order_id,
                        payment_id,
                        referred_user_id,
                    )
        except Exception as e:
            logger.error(f"❌ append_license_history user={user_id}: {e}", exc_info=True)

    async def get_license_history_previous_expires_for_payment(
        self, user_id: int, payment_id: int
    ) -> Optional[datetime]:
        """previous_expires_at из license_history для конкретной оплаты."""
        try:
            async with self.get_connection() as conn:
                return await conn.fetchval(
                    """
                    SELECT previous_expires_at
                    FROM license_history
                    WHERE user_id = $1 AND payment_id = $2
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    user_id,
                    payment_id,
                )
        except Exception as e:
            logger.error(
                "get_license_history_previous_expires_for_payment uid=%s pay=%s: %s",
                user_id,
                payment_id,
                e,
            )
            return None

    async def get_last_subscription_expired_at(
        self, user_id: int, *, before: Optional[datetime] = None
    ) -> Optional[datetime]:
        """Когда лицензию последний раз помечали expired (≈ выход из клуба)."""
        try:
            async with self.get_connection() as conn:
                if before is not None:
                    return await conn.fetchval(
                        """
                        SELECT created_at FROM license_history
                        WHERE user_id = $1
                          AND source = 'subscription_expired'
                          AND created_at < $2
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        user_id,
                        before,
                    )
                return await conn.fetchval(
                    """
                    SELECT created_at FROM license_history
                    WHERE user_id = $1 AND source = 'subscription_expired'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    user_id,
                )
        except Exception as e:
            logger.error(
                "get_last_subscription_expired_at uid=%s: %s", user_id, e
            )
            return None

    async def create_or_extend_license(
        self,
        user_id: int,
        order_id: int,
        expires_at: datetime,
        license_type: str = "subscription",
        *,
        audit_source: str = "subscription_payment",
        audit_payment_id: Optional[int] = None,
        audit_order_id: Optional[int] = None,
    ) -> bool:
        """Создаёт или обновляет лицензию пользователя.

        У каждого пользователя — одна запись в `license`; обновляем expires_at и статус.
        """
        try:
            prev_expires: Optional[datetime] = None
            async with self.get_connection() as conn:
                row_prev = await conn.fetchrow(
                    "SELECT expires_at FROM license WHERE user_id = $1",
                    user_id,
                )
                if row_prev:
                    prev_expires = row_prev["expires_at"]

                existing = await conn.fetchrow(
                    "SELECT id FROM license WHERE user_id = $1",
                    user_id,
                )
                if existing:
                    await conn.execute(
                        """
                        UPDATE license
                        SET expires_at = $1,
                            status = 'active',
                            license_type = $2,
                            payment_id = $3,
                            updated_at = NOW()
                        WHERE user_id = $4
                        """,
                        expires_at,
                        license_type,
                        order_id,
                        user_id,
                    )
                    logger.info(f"✅ License updated for user {user_id} until {expires_at}")
                else:
                    await conn.execute(
                        """
                        INSERT INTO license
                        (user_id, license_type, expires_at, payment_id, status)
                        VALUES ($1, $2, $3, $4, 'active')
                        """,
                        user_id,
                        license_type,
                        expires_at,
                        order_id,
                    )
                    logger.info(f"✅ New license created for user {user_id} until {expires_at}")

            await self.append_license_history(
                user_id=user_id,
                previous_expires_at=prev_expires,
                new_expires_at=expires_at,
                source=audit_source,
                order_id=audit_order_id if audit_order_id is not None else order_id,
                payment_id=audit_payment_id,
                meta={"license_type": license_type},
            )
            return True
        except Exception as e:
            logger.error(f"❌ Failed to create/extend license for user {user_id}: {e}")
            return False

    # =====================================================
    # Бонусные продления / истечения / конвертация
    # =====================================================

    async def get_user_active_license_subscription(
        self, user_id: int
    ) -> Optional[Dict[str, Any]]:
        """Активная лицензия типа 'subscription' (без bonus_extension)."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM license
                    WHERE user_id = $1
                      AND status = 'active'
                      AND license_type = 'subscription'
                      AND expires_at > NOW()
                    ORDER BY expires_at DESC
                    LIMIT 1
                    """,
                    user_id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get active subscription for user {user_id}: {e}")
            return None

    async def extend_license_by_days(
        self,
        user_id: int,
        days: int,
        *,
        audit_referred_user_id: Optional[int] = None,
    ) -> bool:
        """Продлевает (или создаёт) лицензию на `days` дней (тип 'bonus')."""
        try:
            now = datetime.now()
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT expires_at
                      FROM license
                     WHERE user_id = $1 AND status = 'active'
                     ORDER BY expires_at DESC NULLS LAST
                     LIMIT 1
                    """,
                    user_id,
                )
                prev_expires = row["expires_at"] if row else None

                if row and row["expires_at"] and row["expires_at"] > now:
                    new_expiry = row["expires_at"] + timedelta(days=days)
                    logger.info(
                        f"📅 Extending license for user {user_id} from "
                        f"{row['expires_at']} to {new_expiry}"
                    )
                    await conn.execute(
                        """
                        UPDATE license
                           SET expires_at = $1,
                               updated_at = NOW()
                         WHERE user_id = $2 AND status = 'active'
                        """,
                        new_expiry,
                        user_id,
                    )
                else:
                    new_expiry = now + timedelta(days=days)
                    logger.info(
                        f"📅 Creating new license for user {user_id} until {new_expiry}"
                    )
                    await conn.execute(
                        """
                        INSERT INTO license (user_id, license_type, expires_at, status)
                        VALUES ($1, 'bonus', $2, 'active')
                        """,
                        user_id,
                        new_expiry,
                    )

            await self.append_license_history(
                user_id=user_id,
                previous_expires_at=prev_expires,
                new_expires_at=new_expiry,
                source="referral_bonus",
                referred_user_id=audit_referred_user_id,
                meta={"days_added": days},
            )
            return True
        except Exception as e:
            logger.error(f"❌ Failed to extend license: {e}")
            return False

    async def grant_admin_gift_license(
        self,
        user_id: int,
        days: int,
        *,
        admin_telegram_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Выдать или продлить лицензию админом (без оплаты)."""
        if days < 1:
            return None
        now = datetime.now()
        try:
            prev_expires: Optional[datetime] = None
            was_extension = False
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT expires_at, status
                    FROM license
                    WHERE user_id = $1
                    """,
                    user_id,
                )
                if row:
                    prev_expires = row["expires_at"]
                    was_extension = bool(
                        row["status"] == "active"
                        and prev_expires
                        and prev_expires > now
                    )
                    base_date = prev_expires if was_extension else now
                else:
                    base_date = now

                new_expiry = base_date + timedelta(days=days)

                if row:
                    await conn.execute(
                        """
                        UPDATE license
                        SET expires_at = $1,
                            status = 'active',
                            license_type = 'admin_grant',
                            updated_at = NOW()
                        WHERE user_id = $2
                        """,
                        new_expiry,
                        user_id,
                    )
                    logger.info(
                        "✅ Admin gift: license updated uid=%s until %s",
                        user_id,
                        new_expiry,
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO license
                            (user_id, license_type, expires_at, payment_id, status)
                        VALUES ($1, 'admin_grant', $2, NULL, 'active')
                        """,
                        user_id,
                        new_expiry,
                    )
                    logger.info(
                        "✅ Admin gift: new license uid=%s until %s",
                        user_id,
                        new_expiry,
                    )

            await self.append_license_history(
                user_id=user_id,
                previous_expires_at=prev_expires,
                new_expires_at=new_expiry,
                source="admin_grant",
                meta={"days_added": days, "admin_telegram_id": admin_telegram_id},
            )
            return {
                "user_id": user_id,
                "previous_expires_at": prev_expires,
                "new_expires_at": new_expiry,
                "was_extension": was_extension,
                "days": days,
            }
        except Exception as e:
            logger.error(
                "❌ grant_admin_gift_license uid=%s days=%s: %s",
                user_id,
                days,
                e,
                exc_info=True,
            )
            return None

    async def grant_admin_subscription(
        self,
        user_id: int,
        *,
        admin_telegram_id: int,
        expires_at: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Постоянная админская подписка (license_type=admin_subscription).
        Не попадает в цепочку напоминаний об окончании.
        """
        if expires_at is None:
            expires_at = datetime(2099, 12, 31, 23, 59, 59)
        try:
            prev_expires: Optional[datetime] = None
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    "SELECT expires_at FROM license WHERE user_id = $1",
                    user_id,
                )
                if row:
                    prev_expires = row["expires_at"]
                    await conn.execute(
                        """
                        UPDATE license
                        SET expires_at = $1,
                            status = 'active',
                            license_type = $2,
                            updated_at = NOW()
                        WHERE user_id = $3
                        """,
                        expires_at,
                        LICENSE_TYPE_ADMIN_SUBSCRIPTION,
                        user_id,
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO license
                            (user_id, license_type, expires_at, payment_id, status)
                        VALUES ($1, $2, $3, NULL, 'active')
                        """,
                        user_id,
                        LICENSE_TYPE_ADMIN_SUBSCRIPTION,
                        expires_at,
                    )

            await self.append_license_history(
                user_id=user_id,
                previous_expires_at=prev_expires,
                new_expires_at=expires_at,
                source="admin_subscription_grant",
                meta={"admin_telegram_id": admin_telegram_id},
            )
            logger.info(
                "✅ Admin subscription granted uid=%s until %s by admin=%s",
                user_id,
                expires_at,
                admin_telegram_id,
            )
            return {
                "user_id": user_id,
                "license_type": LICENSE_TYPE_ADMIN_SUBSCRIPTION,
                "expires_at": expires_at,
            }
        except Exception as e:
            logger.error(
                "❌ grant_admin_subscription uid=%s: %s", user_id, e, exc_info=True
            )
            return None

    async def get_active_subscriptions_expiring_on(
        self, target_date: "date"
    ) -> List[Dict[str, Any]]:
        """Активные подписки (subscription/admin_grant), дата окончания в МСК = target_date."""
        try:
            types = list(REMINDER_ELIGIBLE_LICENSE_TYPES)
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT l.user_id, l.expires_at, u.first_name
                    FROM license l
                    JOIN users u ON l.user_id = u.user_id
                    WHERE l.status = 'active'
                      AND l.license_type = ANY($2::text[])
                      AND (timezone('Europe/Moscow', l.expires_at))::date = $1::date
                      AND u.is_active = TRUE
                    """,
                    target_date,
                    types,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get expiring subscriptions: {e}")
            return []

    async def get_expired_subscriptions_for_bonus(
        self, target_date: "date"
    ) -> List[Dict[str, Any]]:
        """Подписки/admin_grant: дата окончания (МСК) = target_date — кандидаты на бонусный +1 день."""
        try:
            types = list(REMINDER_ELIGIBLE_LICENSE_TYPES)
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM license
                    WHERE status = 'active'
                      AND license_type = ANY($2::text[])
                      AND (timezone('Europe/Moscow', expires_at))::date = $1::date
                    """,
                    target_date,
                    types,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get expired subscriptions: {e}")
            return []

    async def convert_to_bonus_license(
        self, user_id: int, new_expiry: datetime
    ) -> bool:
        """Конвертирует подписку в бонусное продление."""
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT expires_at, license_type
                      FROM license
                     WHERE user_id = $1 AND status = 'active'
                     LIMIT 1
                    """,
                    user_id,
                )
                if not row:
                    logger.warning(
                        "convert_to_bonus_license: нет активной лицензии uid=%s", user_id
                    )
                    return False

                prev_expires = row["expires_at"]
                prior_type = row.get("license_type")
                if prior_type in (
                    LICENSE_TYPE_BONUS_EXTENSION,
                    LICENSE_TYPE_ADMIN_SUBSCRIPTION,
                ):
                    logger.info(
                        "convert_to_bonus_license: skip uid=%s type=%s",
                        user_id,
                        prior_type,
                    )
                    return False

                # expires_at в БД — обычно TIMESTAMP WITHOUT TIME ZONE: только naive UTC,
                # иначе asyncpg при кодировании даёт naive/aware TypeError.
                nz_license = _to_naive_utc(new_expiry)
                pz_hist = _to_utc_aware(prev_expires)
                nz_hist = _to_utc_aware(new_expiry)

                await conn.execute(
                    """
                    UPDATE license
                    SET expires_at = $2,
                        license_type = 'bonus_extension',
                        updated_at = NOW()
                    WHERE user_id = $1 AND status = 'active'
                    """,
                    user_id,
                    nz_license,
                )

            await self.append_license_history(
                user_id=user_id,
                previous_expires_at=pz_hist,
                new_expires_at=nz_hist,
                source="bonus_extension_offer",
                meta=(
                    {"prior_license_type": prior_type}
                    if prior_type is not None
                    else None
                ),
            )
            return True
        except Exception as e:
            logger.error(f"❌ Failed to convert to bonus license: {e}")
            return False

    async def get_expired_bonus_licenses(
        self, target_date: "date"
    ) -> List[Dict[str, Any]]:
        """Бонусные лицензии, дата окончания периода (МСК) = target_date (кик из группы)."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM license
                    WHERE status = 'active'
                      AND license_type = 'bonus_extension'
                      AND (timezone('Europe/Moscow', expires_at))::date = $1::date
                    """,
                    target_date,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to get expired bonus licenses: {e}")
            return []

    async def list_users_churn_exit_anchor_msk(
        self, last_exit_calendar_msk: "date"
    ) -> List[Dict[str, Any]]:
        """
        Пользователи после полного выхода: лицензия expired, календарная дата
        завершения последнего периода (МСК) совпадает с днём выхода из клуба.
        """
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT u.user_id, u.first_name
                    FROM license l
                    JOIN users u ON u.user_id = l.user_id
                    WHERE l.status = 'expired'
                      AND (timezone('Europe/Moscow', l.expires_at))::date = $1::date
                      AND u.is_active = TRUE
                    """,
                    last_exit_calendar_msk,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(
                "❌ list_users_churn_exit_anchor_msk: %s",
                e,
                exc_info=True,
            )
            return []

    async def expire_stale_active_licenses(self, *, grace_days: int = 3) -> int:
        """Переводит в expired active-лицензии после окна отсрочки.

        Использует ту же логику, что ``club_nightly_audit_should_remove_member``:
        ``expires_at`` должен быть раньше, чем (сейчас − grace_days). Так бонусный
        +1 день (9:00 МСК на следующий календарный день) успевает выдаться, пока
        ``status`` ещё ``active``.
        """
        grace_days = max(0, int(grace_days))
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT user_id
                    FROM license
                    WHERE status = 'active'
                      AND expires_at <= NOW() - make_interval(days => $1::int)
                    ORDER BY user_id
                    """,
                    grace_days,
                )
            fixed = 0
            for row in rows:
                if await self.mark_license_expired(int(row["user_id"])):
                    fixed += 1
            if fixed:
                logger.info(
                    "✅ expire_stale_active_licenses: переведено в expired: %s",
                    fixed,
                )
            return fixed
        except Exception as e:
            logger.error("❌ expire_stale_active_licenses: %s", e, exc_info=True)
            return 0

    async def mark_license_expired(self, user_id: int) -> bool:
        """Помечает активную лицензию пользователя как истёкшую."""
        prev_expires: Optional[datetime] = None
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT expires_at FROM license
                     WHERE user_id = $1 AND status = 'active'
                     LIMIT 1
                    """,
                    user_id,
                )
                if not row:
                    return True

                prev_expires = row["expires_at"]

                await conn.execute(
                    """
                    UPDATE license
                       SET status = 'expired', updated_at = NOW()
                     WHERE user_id = $1 AND status = 'active'
                    """,
                    user_id,
                )

            if prev_expires is not None:
                await self.append_license_history(
                    user_id=user_id,
                    previous_expires_at=prev_expires,
                    new_expires_at=prev_expires,
                    source="subscription_expired",
                    meta={"status_change": "active_to_expired"},
                )
            return True
        except Exception as e:
            logger.error(f"❌ Failed to mark license expired: {e}")
            return False
