"""Единый механизм отзыва доступа: оба кика ходят через него.

Повод — аудит: плановое исключение (9 МСК) и ночной аудит (21:00 UTC) были
двумя независимыми кусками кода со своими условиями. На 24 октября 2026
десять человек оказались исключены обоими по очереди, а отсрочку по просьбе
ДДД знал только плановый — аудит её отменял, потому что его порог короче.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from bot.club_access import (
    REASON_AUDIT,
    REASON_SCHEDULED,
    SOURCE_AUDIT,
    SOURCE_SCHEDULED,
    ClubAccessRevoker,
    RevokeOutcome,
)

NOW = datetime.now(timezone.utc)


class _Conn:
    def __init__(self, recent):
        self._recent = recent

    async def __aenter__(self):
        c = AsyncMock()
        c.fetchval = AsyncMock(return_value=1 if self._recent else None)
        return c

    async def __aexit__(self, *exc):
        return False


def _revoker(*, wish=None, active=False, admin=False, recent=False):
    st = AsyncMock()
    st.is_telegram_admin_id = AsyncMock(return_value=admin)
    st.user_has_active_license = AsyncMock(return_value=active)
    st.wish_active_for_requester = AsyncMock(return_value=wish)
    st.get_connection = MagicMock(return_value=_Conn(recent))
    st.record_club_member_exclusion = AsyncMock()
    st.mark_license_expired = AsyncMock()
    st.delete_club_member_cache = AsyncMock()
    bot = AsyncMock()
    return ClubAccessRevoker(st, bot), st, bot


def _run(c):
    return asyncio.run(c)


def _wish(**kw):
    base = {"id": 58, "status": "taken", "donor_user_id": 7}
    base.update(kw)
    return base


def _args(source=SOURCE_SCHEDULED, reason=REASON_SCHEDULED):
    return {"reason": reason, "source": source, "expires_at": NOW}


def test_removes_when_nothing_blocks():
    r, st, bot = _revoker()
    d = _run(r.revoke(42, **_args()))
    assert d.outcome is RevokeOutcome.REMOVED
    bot.ban_chat_member.assert_awaited_once()
    st.record_club_member_exclusion.assert_awaited_once()
    st.mark_license_expired.assert_awaited_once()


def test_wish_holds_both_mechanisms():
    """Главное: отсрочку видит и плановый, и аварийный."""
    for source, reason in (
        (SOURCE_SCHEDULED, REASON_SCHEDULED),
        (SOURCE_AUDIT, REASON_AUDIT),
    ):
        r, _, bot = _revoker(wish=_wish())
        d = _run(r.revoke(42, **_args(source, reason)))
        assert d.outcome is RevokeOutcome.HELD_BY_WISH, source
        assert d.wish and d.wish["id"] == 58
        bot.ban_chat_member.assert_not_awaited()


def test_no_double_kick_by_second_mechanism():
    """Десять человек были исключены обоими механизмами подряд."""
    r, _, bot = _revoker(recent=True)
    d = _run(r.revoke(42, **_args(SOURCE_AUDIT, REASON_AUDIT)))
    assert d.outcome is RevokeOutcome.SKIPPED_RECENTLY_REMOVED
    bot.ban_chat_member.assert_not_awaited()


def test_active_license_stops_removal():
    """Доступ мог появиться между решением и действием — например, оплатили."""
    r, _, bot = _revoker(active=True)
    d = _run(r.revoke(42, **_args()))
    assert d.outcome is RevokeOutcome.SKIPPED_ACTIVE_LICENSE
    bot.ban_chat_member.assert_not_awaited()


def test_admin_never_removed():
    r, _, bot = _revoker(admin=True)
    assert _run(r.revoke(42, **_args())).outcome is RevokeOutcome.SKIPPED_ADMIN
    bot.ban_chat_member.assert_not_awaited()


def test_grace_expired_lets_removal_through():
    r, _, bot = _revoker(wish=_wish())
    old = NOW - timedelta(days=30)
    d = _run(r.revoke(42, reason=REASON_SCHEDULED, source=SOURCE_SCHEDULED,
                      expires_at=old))
    assert d.outcome is RevokeOutcome.REMOVED
    bot.ban_chat_member.assert_awaited_once()


def test_broken_wish_does_not_hold():
    r, _, bot = _revoker(wish={"id": None, "status": "taken"})
    assert _run(r.revoke(42, **_args())).outcome is RevokeOutcome.REMOVED


def test_decide_changes_nothing():
    r, st, bot = _revoker()
    d = _run(r.decide(42, reason=REASON_SCHEDULED, source=SOURCE_SCHEDULED))
    assert d.outcome is RevokeOutcome.REMOVED
    bot.ban_chat_member.assert_not_awaited()
    st.record_club_member_exclusion.assert_not_awaited()


def test_kick_failure_reported():
    r, st, bot = _revoker()
    bot.ban_chat_member = AsyncMock(side_effect=RuntimeError("chat not found"))
    d = _run(r.revoke(42, **_args()))
    assert d.outcome is RevokeOutcome.FAILED
    # журнал не трогаем, раз человек остался в чате
    st.record_club_member_exclusion.assert_not_awaited()
