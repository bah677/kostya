"""Тесты синхронизации устаревших active-лицензий."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from storage.db.licenses import LicensesMixin


class _Storage(LicensesMixin):
    def __init__(self):
        self._conn = MagicMock()


def test_expire_stale_active_licenses_marks_each_user():
    storage = _Storage()
    conn = AsyncMock()
    conn.fetch = AsyncMock(
        return_value=[{"user_id": 101}, {"user_id": 202}],
    )
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    storage.get_connection = MagicMock(return_value=cm)

    with patch.object(
        storage,
        "mark_license_expired",
        new_callable=AsyncMock,
        side_effect=[True, True],
    ) as mark:
        fixed = asyncio.run(storage.expire_stale_active_licenses(grace_days=3))

    assert fixed == 2
    assert mark.await_count == 2
    mark.assert_any_await(101)
    mark.assert_any_await(202)


def test_expire_stale_active_licenses_empty():
    storage = _Storage()
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    storage.get_connection = MagicMock(return_value=cm)

    with patch.object(storage, "mark_license_expired", new_callable=AsyncMock) as mark:
        fixed = asyncio.run(storage.expire_stale_active_licenses(grace_days=3))

    assert fixed == 0
    mark.assert_not_awaited()
