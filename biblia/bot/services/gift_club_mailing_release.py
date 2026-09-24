"""При отмене gift-черновика в Библии — снять резерв в club.gift_mailing_sent."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

import asyncpg

logger = logging.getLogger(__name__)

_CAMPAIGN = "gift-2026-09"
_CLUB_ENV_CANDIDATES = (
    Path("/home/appuser/club/.env"),
    Path("/home/appuser/dev/kostya/club/.env"),
)


def _load_env_file(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _club_dsns() -> List[str]:
    seen = set()
    out: List[str] = []
    for path in _CLUB_ENV_CANDIDATES:
        env = _load_env_file(path)
        host = env.get("DB_HOST") or ""
        name = env.get("DB_NAME") or ""
        user = env.get("DB_USER") or ""
        password = env.get("DB_PASSWORD") or ""
        if not (host and name and user and password):
            continue
        port = int(env.get("DB_PORT") or 5432)
        dsn = f"postgresql://{user}:{password}@{host}:{port}/{name}"
        if dsn in seen:
            continue
        seen.add(dsn)
        out.append(dsn)
    return out


async def release_club_gift_reservation_from_biblia(
    *,
    campaign_id: int,
    campaign_name: str,
    audience_user_ids: Optional[List[int]] = None,
) -> int:
    """Удаляет gift_mailing_sent в клубе для аудитории отменённой biblia-кампании."""
    if not str(campaign_name or "").startswith(_CAMPAIGN):
        return 0
    uids = [int(u) for u in (audience_user_ids or []) if int(u) > 0]
    if not uids:
        return 0
    dsns = _club_dsns()
    if not dsns:
        logger.warning(
            "gift release: нет club DB в %s — резерв не снят #%s",
            _CLUB_ENV_CANDIDATES,
            campaign_id,
        )
        return 0
    freed_total = 0
    for dsn in dsns:
        try:
            conn = await asyncpg.connect(dsn)
            try:
                result = await conn.execute(
                    """
                    DELETE FROM gift_mailing_sent
                    WHERE campaign = $1
                      AND user_id = ANY($2::bigint[])
                      AND cohort <> 'TEST'
                    """,
                    _CAMPAIGN,
                    uids,
                )
                try:
                    freed_total += int(result.split()[-1])
                except Exception:
                    pass
            finally:
                await conn.close()
        except Exception as e:
            logger.warning("gift release club #%s: %s", campaign_id, e)
    return freed_total