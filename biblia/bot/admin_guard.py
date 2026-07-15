"""
Проверка строки admins в БД с коротким TTL-кэшем в процессе.
"""

from __future__ import annotations

import time
from typing import Dict, Tuple

from storage.user_storage import UserStorage

_CACHE: Dict[int, Tuple[bool, float]] = {}
_TTL_SEC = 45.0


async def is_telegram_admin(user_storage: UserStorage, telegram_user_id: int) -> bool:
    now = time.monotonic()
    hit = _CACHE.get(telegram_user_id)
    if hit is not None:
        ok, ts = hit
        if now - ts < _TTL_SEC:
            return ok
    ok = await user_storage.is_telegram_admin_id(telegram_user_id)
    _CACHE[telegram_user_id] = (ok, now)
    return ok


def invalidate_admin_cache(telegram_user_id: int) -> None:
    _CACHE.pop(telegram_user_id, None)
