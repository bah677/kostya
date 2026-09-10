#!/usr/bin/env python3
"""
Backfill club_first_week для вошедших в группу за последние N дней.

Не шлёт прошлые шаги пачкой: выставляет step = уже «прошедшие» по времени,
чтобы агент слал только оставшиеся касания.

  cd /home/appuser/dev/kostya/club
  python3 scripts/backfill_club_first_week.py --dry-run
  python3 scripts/backfill_club_first_week.py --apply
  # на прод-БД после деплоя:
  python3 scripts/backfill_club_first_week.py --env-file /home/appuser/club/.env --dry-run
  python3 scripts/backfill_club_first_week.py --env-file /home/appuser/club/.env --apply
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.services.club_first_week_steps import catchup_completed_step
from config import load_config
from storage.user_storage import UserStorage

logger = logging.getLogger("backfill_club_first_week")
MSK = ZoneInfo("Europe/Moscow")


def _load_config(env_file: str):
    if env_file:
        from dotenv import load_dotenv

        load_dotenv(env_file, override=True)
    return load_config()


async def _candidates(
    storage: UserStorage, *, club_group_id: int, days: int
) -> List[Dict[str, Any]]:
    """Первое сообщение / вход по волне за окно days + активная лицензия."""
    async with storage.get_connection() as conn:
        rows = await conn.fetch(
            """
            WITH first_group AS (
                SELECT m.user_id, MIN(m.created_at) AS started_at
                FROM messages m
                WHERE m.chat_id = $1
                  AND m.role = 'user'
                  AND m.deleted_at IS NULL
                  AND COALESCE(TRIM(m.content), '') <> ''
                GROUP BY m.user_id
                HAVING MIN(m.created_at) > NOW() - make_interval(days => $2)
            ),
            wave_joins AS (
                SELECT gwm.user_id, MIN(gwm.joined_at) AS started_at
                FROM gift_wave_member gwm
                WHERE gwm.joined_at IS NOT NULL
                  AND gwm.joined_at > NOW() - make_interval(days => $2)
                GROUP BY gwm.user_id
            ),
            starts AS (
                SELECT user_id, started_at FROM first_group
                UNION ALL
                SELECT user_id, started_at FROM wave_joins
            ),
            best AS (
                SELECT user_id, MIN(started_at) AS started_at
                FROM starts
                GROUP BY user_id
            )
            SELECT
                b.user_id,
                b.started_at,
                l.origin,
                l.expires_at AS deadline_at,
                l.license_type,
                COALESCE((
                    SELECT COUNT(*)::int
                    FROM messages m2
                    WHERE m2.user_id = b.user_id
                      AND m2.chat_id = $1
                      AND m2.role = 'user'
                      AND m2.deleted_at IS NULL
                      AND COALESCE(TRIM(m2.content), '') <> ''
                      AND m2.created_at >= b.started_at
                      AND m2.created_at < b.started_at + INTERVAL '7 days'
                ), 0) AS msgs_group
            FROM best b
            JOIN license l ON l.user_id = b.user_id
                AND l.status = 'active'
                AND l.expires_at > NOW()
            LEFT JOIN club_first_week cfw ON cfw.user_id = b.user_id
            WHERE cfw.user_id IS NULL
            ORDER BY b.started_at ASC
            """,
            club_group_id,
            days,
        )
        return [dict(r) for r in rows]


async def run(*, apply: bool, days: int, env_file: str) -> None:
    cfg = _load_config(env_file)
    club_gid = int(cfg.CLUB_GROUP_ID or 0)
    if not club_gid:
        raise SystemExit("CLUB_GROUP_ID не задан")

    storage = UserStorage(cfg.database_url)
    await storage.connect()
    try:
        rows = await _candidates(storage, club_group_id=club_gid, days=days)
        logger.info("кандидатов на backfill: %s (days=%s)", len(rows), days)
        now = datetime.now(MSK)
        inserted = 0
        for r in rows:
            uid = int(r["user_id"])
            started = r["started_at"]
            if started.tzinfo is None:
                started = started.replace(tzinfo=MSK)
            msgs = int(r["msgs_group"] or 0)
            origin = str(r.get("origin") or "payment")
            if origin == "payment" and str(r.get("license_type") or "") == "admin_grant":
                origin = "gift"
            deadline = r.get("deadline_at")
            step = catchup_completed_step(started, now=now)
            age_h = (now - started.astimezone(MSK)).total_seconds() / 3600.0
            end_now = age_h >= 7 * 24
            done_at: Optional[datetime] = now if msgs >= 3 else None
            logger.info(
                "uid=%s started=%s msgs=%s step=%s origin=%s end=%s done=%s",
                uid,
                started.isoformat(),
                msgs,
                step,
                origin,
                end_now,
                bool(done_at),
            )
            if not apply:
                continue
            await storage.ensure_member_profile(uid)
            await storage.set_member_onboarding_stage(uid, "started")
            ok = await storage.start_club_first_week(
                uid,
                origin=origin,
                started_at=started,
                deadline_at=deadline,
            )
            if not ok:
                continue
            async with storage.get_connection() as conn:
                await conn.execute(
                    """
                    UPDATE club_first_week
                    SET step = $2,
                        msgs_group = $3,
                        done_at = $4,
                        ended_at = CASE WHEN $5 THEN NOW() ELSE ended_at END,
                        last_step_at = NOW(),
                        updated_at = NOW()
                    WHERE user_id = $1
                    """,
                    uid,
                    step,
                    msgs,
                    done_at,
                    end_now,
                )
            await storage.assign_step4_variant(uid)
            try:
                await storage.log_interaction(
                    user_id=uid,
                    event_category="first_week",
                    event_type="week1_started",
                    data={
                        "origin": origin,
                        "backfill": True,
                        "step_catchup": step,
                        "msgs_group": msgs,
                    },
                    source="backfill_club_first_week",
                    outcome="success",
                )
            except Exception:
                pass
            inserted += 1
        logger.info(
            "готово apply=%s inserted=%s / candidates=%s",
            apply,
            inserted,
            len(rows),
        )
    finally:
        await storage.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="только показать (по умолчанию, если нет --apply)",
    )
    p.add_argument("--apply", action="store_true", help="записать в БД")
    p.add_argument("--days", type=int, default=7)
    p.add_argument(
        "--env-file",
        default="",
        help="путь к .env (пусто = текущий club/.env)",
    )
    args = p.parse_args()
    apply = bool(args.apply)
    asyncio.run(run(apply=apply, days=args.days, env_file=args.env_file))


if __name__ == "__main__":
    main()
