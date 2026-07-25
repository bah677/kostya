"""Доступ к agency-боту: SUPER_ADMIN_ID + таблица admins (+ опционально AGENCY_ADMIN_IDS)."""

from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

from config import Config
from db import admins as admins_db

_CACHE: Dict[int, Tuple[bool, float]] = {}
_TTL_SEC = 45.0


def is_super_admin(cfg: Config, user_id: int | None) -> bool:
    if user_id is None:
        return False
    sid = int(getattr(cfg, "SUPER_ADMIN_ID", 0) or 0)
    return bool(sid) and int(user_id) == sid


def is_env_bootstrap_admin(cfg: Config, user_id: int | None) -> bool:
    """Запасной список из .env (не для постоянного управления — есть /admin_add)."""
    if user_id is None:
        return False
    return int(user_id) in (cfg.AGENCY_ADMIN_IDS or ())


async def is_agency_admin(cfg: Config, pool, user_id: int | None) -> bool:
    if user_id is None:
        return False
    uid = int(user_id)
    if is_super_admin(cfg, uid):
        return True
    if is_env_bootstrap_admin(cfg, uid):
        return True
    now = time.monotonic()
    hit = _CACHE.get(uid)
    if hit is not None:
        ok, ts = hit
        if now - ts < _TTL_SEC:
            return ok
    ok = False
    if pool is not None:
        ok = await admins_db.is_telegram_admin_id(pool, uid)
    _CACHE[uid] = (ok, now)
    return ok


def invalidate_admin_cache(user_id: Optional[int] = None) -> None:
    if user_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(int(user_id), None)


# backward-compatible sync stub (unused) — keep name for imports that expected sync
def is_agency_admin_sync(cfg: Config, user_id: int | None) -> bool:
    return is_super_admin(cfg, user_id) or is_env_bootstrap_admin(cfg, user_id)
