#!/usr/bin/env python3
"""Собрать HTML-отчёт по розыгрышу gift-2026-09 → reports.mironbot.ru.

Админы исключены из всех цифр. Пишет в www/reports/.../54f1f46d2d/

  ./venv/bin/python3 scripts/build_gift_campaign_report.py
  ./venv/bin/python3 scripts/build_gift_campaign_report.py --out /path/to/index.html
"""

from __future__ import annotations

import argparse
import html
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

import asyncpg

MSK = ZoneInfo("Europe/Moscow")
CAMPAIGN = "gift-2026-09"
TICKET_CAP = 150
DEFAULT_OUT = Path(
    "/home/appuser/www/reports/45537a5b39cd8c302855/6fc916b25071/54f1f46d2d/index.html"
)
CLUB_ENV = Path("/home/appuser/club/.env")
BIBLIA_ENV = Path("/home/appuser/biblia/.env")


def _load_env(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        # skip values with unescaped shell junk by taking first '=' only
        k, _, v = line.partition("=")
        k = k.strip()
        if not re.match(r"^[A-Z0-9_]+$", k):
            continue
        out[k] = v.strip().strip('"').strip("'")
    return out


async def _connect(env: Dict[str, str]) -> asyncpg.Connection:
    return await asyncpg.connect(
        host=env.get("DB_HOST") or "localhost",
        port=int(env.get("DB_PORT") or 5432),
        user=env["DB_USER"],
        password=env["DB_PASSWORD"],
        database=env["DB_NAME"],
    )


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", "\u202f")


def _pct(part: int, whole: int) -> str:
    if whole <= 0:
        return "—"
    return f"{100.0 * part / whole:.0f}%"


def _bar_row(label: str, value: int, max_v: int, *, hint: str = "") -> str:
    w = 0 if max_v <= 0 else max(2, int(round(100 * value / max_v)))
    hint_html = f'<span class="hint">{html.escape(hint)}</span>' if hint else ""
    return (
        f'<div class="bar-row">'
        f'<div class="bar-lab">{html.escape(label)}</div>'
        f'<div class="bar-track"><div class="bar-fill" style="width:{w}%"></div></div>'
        f'<div class="bar-val">{_fmt(value)}</div>{hint_html}'
        f"</div>"
    )


async def collect() -> Dict[str, Any]:
    club_env = _load_env(CLUB_ENV)
    bib_env = _load_env(BIBLIA_ENV)
    gid = int(club_env.get("CLUB_GROUP_ID") or 0)
    super_id = int(club_env.get("SUPER_ADMIN_ID") or 0)

    conn = await _connect(club_env)
    try:
        admin_rows = await conn.fetch("SELECT telegram_user_id AS user_id FROM admins")
        admins: Set[int] = {int(r["user_id"]) for r in admin_rows}
        if super_id > 0:
            admins.add(super_id)
        admin_list = sorted(admins)

        t1_days = await conn.fetch(
            """
            SELECT (ma.updated_at AT TIME ZONE 'Europe/Moscow')::date AS day,
                   substring(mc.name from 'Т1 ([A-Z0-9]+)') AS cohort,
                   COUNT(*) FILTER (WHERE ma.status = 'sent')::int AS sent,
                   COUNT(*) FILTER (WHERE ma.status = 'blocked')::int AS blocked
            FROM mailing_audience ma
            JOIN mailing_campaigns mc ON mc.id = ma.campaign_id
            WHERE mc.name LIKE 'gift-2026-09 Т1%'
              AND mc.status = 'completed'
              AND mc.name NOT LIKE '%TEST%'
              AND ma.user_id != ALL($1::bigint[])
            GROUP BY 1, 2
            ORDER BY 1, 2
            """,
            admin_list,
        )

        apps_days = await conn.fetch(
            """
            SELECT (submitted_at AT TIME ZONE 'Europe/Moscow')::date AS day,
                   COUNT(*)::int AS n
            FROM gift_application
            WHERE campaign = $1
              AND submitted_at IS NOT NULL
              AND user_id != ALL($2::bigint[])
            GROUP BY 1 ORDER BY 1
            """,
            CAMPAIGN,
            admin_list,
        )

        apps_tot = await conn.fetchrow(
            """
            SELECT
              COUNT(*) FILTER (WHERE submitted_at IS NOT NULL)::int AS submitted,
              COUNT(*) FILTER (WHERE status IN ('queued', 'submitted'))::int AS in_queue,
              COUNT(*) FILTER (
                WHERE status = 'submitted' AND COALESCE(verdict, '') = 'review'
              )::int AS waiting_review,
              COUNT(*) FILTER (
                WHERE status = 'queued' AND verdict = 'pass'
              )::int AS ready_draw,
              COUNT(*) FILTER (WHERE status = 'draft')::int AS draft,
              COUNT(*) FILTER (WHERE status = 'expired')::int AS expired,
              COUNT(*) FILTER (WHERE status = 'cancelled')::int AS cancelled,
              COUNT(*)::int AS total
            FROM gift_application
            WHERE campaign = $1 AND user_id != ALL($2::bigint[])
            """,
            CAMPAIGN,
            admin_list,
        )

        queue_ops = await conn.fetchrow(
            """
            SELECT
              COUNT(*) FILTER (
                WHERE ga.status = 'submitted'
                  AND COALESCE(ga.verdict, '') = 'review'
              )::int AS waiting_review,
              COUNT(*) FILTER (
                WHERE ga.status = 'queued' AND ga.verdict = 'pass'
              )::int AS ready_draw,
              COUNT(*) FILTER (
                WHERE ga.status IN ('selected', 'drawn')
                  AND (
                    gwm.status IS NULL
                    OR gwm.status IN ('queued', 'expired', 'declined')
                  )
              )::int AS selected_no_ticket,
              COUNT(*) FILTER (
                WHERE gwm.status = 'queued'
              )::int AS wave_queued,
              COUNT(*) FILTER (
                WHERE gwm.status = 'granted'
              )::int AS ticket_holding,
              COUNT(*) FILTER (
                WHERE gwm.status IN (
                  'granted', 'activated', 'joined', 'spoke'
                )
              )::int AS tickets_occupied
            FROM gift_application ga
            LEFT JOIN gift_wave_member gwm ON gwm.application_id = ga.id
            LEFT JOIN gift_wave w
              ON w.id = gwm.wave_id AND w.campaign = ga.campaign
            WHERE ga.campaign = $1
              AND ga.user_id != ALL($2::bigint[])
            """,
            CAMPAIGN,
            admin_list,
        )

        tickets_hold_all = await conn.fetchrow(
            """
            SELECT
              COUNT(*) FILTER (WHERE gwm.status = 'granted')::int AS holding,
              COUNT(*) FILTER (
                WHERE gwm.status IN (
                  'granted', 'activated', 'joined', 'spoke'
                )
              )::int AS occupied
            FROM gift_wave_member gwm
            JOIN gift_wave w ON w.id = gwm.wave_id
            WHERE w.campaign = $1
              AND gwm.user_id != ALL($2::bigint[])
            """,
            CAMPAIGN,
            admin_list,
        )

        ttl_days = 7
        try:
            from config import config as _cfg

            ttl_days = int(getattr(_cfg, "GIFT_TICKET_TTL_DAYS", 7) or 7)
        except Exception:
            pass

        free_rows = await conn.fetch(
            """
            SELECT
              (
                (gwm.granted_at + ($3 || ' days')::interval)
                AT TIME ZONE 'Europe/Moscow'
              )::date AS free_day,
              COUNT(*)::int AS n
            FROM gift_wave_member gwm
            JOIN gift_wave w ON w.id = gwm.wave_id
            WHERE w.campaign = $1
              AND gwm.user_id != ALL($2::bigint[])
              AND gwm.status = 'granted'
              AND gwm.granted_at IS NOT NULL
            GROUP BY 1
            ORDER BY 1
            """,
            CAMPAIGN,
            admin_list,
            str(ttl_days),
        )

        waves = await conn.fetch(
            """
            SELECT w.wave_index,
                   (w.created_at AT TIME ZONE 'Europe/Moscow')::date AS day,
                   COUNT(*)::int AS won,
                   COUNT(*) FILTER (WHERE gwm.selection = 'score')::int AS by_score,
                   COUNT(*) FILTER (WHERE gwm.selection = 'draw')::int AS by_draw,
                   COUNT(*) FILTER (
                     WHERE gwm.joined_at IS NOT NULL
                        OR gwm.status IN ('activated', 'joined', 'spoke')
                   )::int AS entered,
                   COUNT(*) FILTER (
                     WHERE gwm.status = 'spoke' OR gwm.first_msg_at IS NOT NULL
                   )::int AS wrote,
                   COUNT(*) FILTER (WHERE gwm.status = 'granted')::int AS waiting,
                   COUNT(*) FILTER (
                     WHERE gwm.status = 'activated' AND gwm.first_msg_at IS NULL
                   )::int AS silent
            FROM gift_wave_member gwm
            JOIN gift_wave w ON w.id = gwm.wave_id
            WHERE w.campaign = $1 AND gwm.user_id != ALL($2::bigint[])
            GROUP BY w.wave_index, 2
            ORDER BY w.wave_index
            """,
            CAMPAIGN,
            admin_list,
        )

        entered = await conn.fetch(
            """
            SELECT gwm.user_id, w.wave_index, gwm.status, gwm.granted_at,
                   gwm.joined_at, gwm.first_msg_at,
                   COALESCE(u.username, '') AS username,
                   COALESCE(u.first_name, '') AS first_name,
                   (
                     SELECT COUNT(*)::int FROM messages m
                     WHERE m.chat_id = $3
                       AND m.user_id = gwm.user_id
                       AND m.sender_type = 'user'
                       AND m.telegram_message_id IS NOT NULL
                       AND (gwm.granted_at IS NULL
                            OR m.created_at >= gwm.granted_at - interval '1 day')
                   ) AS msgs
            FROM gift_wave_member gwm
            JOIN gift_wave w ON w.id = gwm.wave_id
            LEFT JOIN users u ON u.user_id = gwm.user_id
            WHERE w.campaign = $1
              AND gwm.user_id != ALL($2::bigint[])
              AND (gwm.joined_at IS NOT NULL
                   OR gwm.status IN ('activated', 'joined', 'spoke'))
            ORDER BY msgs DESC, w.wave_index
            """,
            CAMPAIGN,
            admin_list,
            gid,
        )

        greeter = await conn.fetch(
            """
            SELECT cga.status, COUNT(*)::int AS n
            FROM club_greeter_assignment cga
            WHERE cga.newcomer_id IN (
              SELECT gwm.user_id FROM gift_wave_member gwm
              JOIN gift_wave w ON w.id = gwm.wave_id
              WHERE w.campaign = $1 AND gwm.user_id != ALL($2::bigint[])
            )
            GROUP BY 1 ORDER BY n DESC
            """,
            CAMPAIGN,
            admin_list,
        )

        greeter_people = await conn.fetchrow(
            """
            SELECT
              COUNT(DISTINCT cga.newcomer_id)::int AS people,
              COUNT(*)::int AS assignments,
              ROUND(
                AVG(EXTRACT(EPOCH FROM (cga.replied_at - cga.assigned_at)) / 3600.0)
                  FILTER (WHERE cga.replied_at IS NOT NULL)
              ::numeric, 1) AS avg_reply_h
            FROM club_greeter_assignment cga
            WHERE cga.newcomer_id IN (
              SELECT gwm.user_id FROM gift_wave_member gwm
              JOIN gift_wave w ON w.id = gwm.wave_id
              WHERE w.campaign = $1 AND gwm.user_id != ALL($2::bigint[])
            )
            """,
            CAMPAIGN,
            admin_list,
        )

        greeter_by = await conn.fetch(
            """
            SELECT cga.greeter_id,
                   COALESCE(u.username, '') AS username,
                   LEFT(COALESCE(u.first_name, ''), 24) AS first_name,
                   COUNT(*)::int AS assigned,
                   COUNT(*) FILTER (WHERE cga.status = 'replied')::int AS replied,
                   COUNT(*) FILTER (WHERE cga.status = 'pending')::int AS pending,
                   COUNT(*) FILTER (WHERE cga.status = 'bot_followup')::int AS bot_fu,
                   COUNT(*) FILTER (WHERE cga.status = 'reassigned')::int AS reassigned,
                   ROUND(
                     AVG(EXTRACT(EPOCH FROM (cga.replied_at - cga.assigned_at)) / 60.0)
                       FILTER (WHERE cga.replied_at IS NOT NULL)
                   ::numeric, 0) AS avg_reply_min
            FROM club_greeter_assignment cga
            LEFT JOIN users u ON u.user_id = cga.greeter_id
            WHERE cga.newcomer_id IN (
              SELECT gwm.user_id FROM gift_wave_member gwm
              JOIN gift_wave w ON w.id = gwm.wave_id
              WHERE w.campaign = $1 AND gwm.user_id != ALL($2::bigint[])
            )
            GROUP BY 1, 2, 3
            ORDER BY assigned DESC, replied DESC
            """,
            CAMPAIGN,
            admin_list,
        )

        greeter_no_list = await conn.fetch(
            """
            SELECT gwm.user_id, w.wave_index, gwm.status,
                   gwm.joined_at AT TIME ZONE 'Europe/Moscow' AS joined_msk,
                   gwm.activated_at AT TIME ZONE 'Europe/Moscow' AS activated_msk,
                   COALESCE(u.username, '') AS username,
                   LEFT(COALESCE(u.first_name, ''), 24) AS first_name,
                   ROUND(
                     EXTRACT(EPOCH FROM (NOW() - COALESCE(gwm.joined_at, gwm.activated_at)))
                     / 3600.0
                   ::numeric, 1) AS hours_in_group
            FROM gift_wave_member gwm
            JOIN gift_wave w ON w.id = gwm.wave_id
            LEFT JOIN users u ON u.user_id = gwm.user_id
            WHERE w.campaign = $1
              AND gwm.user_id != ALL($2::bigint[])
              AND (gwm.joined_at IS NOT NULL
                   OR gwm.status IN ('activated', 'joined', 'spoke'))
              AND NOT EXISTS (
                SELECT 1 FROM club_greeter_assignment c
                WHERE c.newcomer_id = gwm.user_id
              )
            ORDER BY COALESCE(gwm.joined_at, gwm.activated_at) NULLS LAST
            """,
            CAMPAIGN,
            admin_list,
        )

        # Итог по новичку, которому назначали: лучший исход + цепочка
        greeter_newcomers = await conn.fetch(
            """
            WITH winners AS (
              SELECT gwm.user_id, w.wave_index, gwm.status AS member_status,
                     gwm.first_msg_at,
                     COALESCE(u.username, '') AS username,
                     LEFT(COALESCE(u.first_name, ''), 24) AS first_name,
                     (
                       SELECT COUNT(*)::int FROM messages m
                       WHERE m.chat_id = $3
                         AND m.user_id = gwm.user_id
                         AND m.sender_type = 'user'
                         AND m.telegram_message_id IS NOT NULL
                         AND (gwm.granted_at IS NULL
                              OR m.created_at >= gwm.granted_at - interval '1 day')
                     ) AS msgs
              FROM gift_wave_member gwm
              JOIN gift_wave w ON w.id = gwm.wave_id
              LEFT JOIN users u ON u.user_id = gwm.user_id
              WHERE w.campaign = $1
                AND gwm.user_id != ALL($2::bigint[])
                AND EXISTS (
                  SELECT 1 FROM club_greeter_assignment c
                  WHERE c.newcomer_id = gwm.user_id
                )
            )
            SELECT w.user_id, w.wave_index, w.member_status, w.username, w.first_name,
                   w.msgs, w.first_msg_at AT TIME ZONE 'Europe/Moscow' AS first_msg_msk,
                   (SELECT COUNT(*)::int FROM club_greeter_assignment c
                    WHERE c.newcomer_id = w.user_id)::int AS attempts,
                   (SELECT BOOL_OR(c.status = 'replied')
                    FROM club_greeter_assignment c WHERE c.newcomer_id = w.user_id) AS got_reply,
                   (SELECT BOOL_OR(c.status = 'bot_followup')
                    FROM club_greeter_assignment c WHERE c.newcomer_id = w.user_id) AS got_bot,
                   (SELECT BOOL_OR(c.status = 'pending')
                    FROM club_greeter_assignment c WHERE c.newcomer_id = w.user_id) AS still_pending,
                   (SELECT MIN(c.assigned_at) AT TIME ZONE 'Europe/Moscow'
                    FROM club_greeter_assignment c WHERE c.newcomer_id = w.user_id) AS first_assign_msk,
                   (SELECT MIN(c.replied_at) AT TIME ZONE 'Europe/Moscow'
                    FROM club_greeter_assignment c
                    WHERE c.newcomer_id = w.user_id AND c.replied_at IS NOT NULL) AS replied_msk,
                   ROUND(
                     (
                       SELECT MIN(EXTRACT(EPOCH FROM (c.replied_at - c.assigned_at)) / 60.0)
                       FROM club_greeter_assignment c
                       WHERE c.newcomer_id = w.user_id AND c.replied_at IS NOT NULL
                     )::numeric, 0
                   ) AS reply_min,
                   (
                     SELECT string_agg(
                       COALESCE(NULLIF(gu.username, ''), LEFT(COALESCE(gu.first_name,''),16), g.greeter_id::text)
                       || '·' || g.status || '·п' || g.attempt::text,
                       ' → ' ORDER BY g.assigned_at
                     )
                     FROM club_greeter_assignment g
                     LEFT JOIN users gu ON gu.user_id = g.greeter_id
                     WHERE g.newcomer_id = w.user_id
                   ) AS chain
            FROM winners w
            ORDER BY w.wave_index, w.first_msg_at NULLS LAST
            """,
            CAMPAIGN,
            admin_list,
            gid,
        )

        greeter_assignments = await conn.fetch(
            """
            SELECT cga.id, cga.status, cga.attempt,
                   cga.assigned_at AT TIME ZONE 'Europe/Moscow' AS assigned_msk,
                   cga.replied_at AT TIME ZONE 'Europe/Moscow' AS replied_msk,
                   cga.reassigned_at AT TIME ZONE 'Europe/Moscow' AS reassigned_msk,
                   ROUND(
                     EXTRACT(EPOCH FROM (
                       COALESCE(cga.replied_at, cga.reassigned_at, NOW()) - cga.assigned_at
                     )) / 60.0
                   ::numeric, 0) AS waited_min,
                   cga.greeter_id,
                   COALESCE(gu.username, '') AS greeter_uname,
                   LEFT(COALESCE(gu.first_name, ''), 20) AS greeter_fname,
                   cga.newcomer_id,
                   COALESCE(nu.username, '') AS newcomer_uname,
                   LEFT(COALESCE(nu.first_name, ''), 20) AS newcomer_fname,
                   w.wave_index
            FROM club_greeter_assignment cga
            JOIN gift_wave_member gwm ON gwm.user_id = cga.newcomer_id
            JOIN gift_wave w ON w.id = gwm.wave_id AND w.campaign = $1
            LEFT JOIN users gu ON gu.user_id = cga.greeter_id
            LEFT JOIN users nu ON nu.user_id = cga.newcomer_id
            WHERE cga.newcomer_id != ALL($2::bigint[])
            ORDER BY cga.assigned_at DESC
            """,
            CAMPAIGN,
            admin_list,
        )

        greeter_pool = await conn.fetch(
            """
            SELECT cg.user_id, cg.active, cg.capacity, cg.miss_streak,
                   cg.paused_until AT TIME ZONE 'Europe/Moscow' AS paused_msk,
                   COALESCE(u.username, '') AS username,
                   LEFT(COALESCE(u.first_name, ''), 24) AS first_name,
                   (SELECT COUNT(*)::int FROM club_greeter_assignment a
                    WHERE a.greeter_id = cg.user_id
                      AND a.status = 'pending')::int AS open_pending,
                   (SELECT COUNT(*)::int FROM club_greeter_assignment a
                    WHERE a.greeter_id = cg.user_id
                      AND a.assigned_at::date = (NOW() AT TIME ZONE 'Europe/Moscow')::date
                   )::int AS today_n
            FROM club_greeter cg
            LEFT JOIN users u ON u.user_id = cg.user_id
            ORDER BY cg.active DESC, cg.miss_streak DESC, cg.user_id
            """
        )

        greeter_outcome = await conn.fetchrow(
            """
            WITH n AS (
              SELECT DISTINCT cga.newcomer_id
              FROM club_greeter_assignment cga
              WHERE cga.newcomer_id IN (
                SELECT gwm.user_id FROM gift_wave_member gwm
                JOIN gift_wave w ON w.id = gwm.wave_id
                WHERE w.campaign = $1 AND gwm.user_id != ALL($2::bigint[])
              )
            )
            SELECT
              (SELECT COUNT(*) FROM n)::int AS with_assign,
              (SELECT COUNT(*) FROM n x WHERE EXISTS (
                 SELECT 1 FROM club_greeter_assignment c
                 WHERE c.newcomer_id = x.newcomer_id AND c.status = 'replied'
               ))::int AS human_ok,
              (SELECT COUNT(*) FROM n x WHERE NOT EXISTS (
                 SELECT 1 FROM club_greeter_assignment c
                 WHERE c.newcomer_id = x.newcomer_id AND c.status = 'replied'
               ) AND EXISTS (
                 SELECT 1 FROM club_greeter_assignment c
                 WHERE c.newcomer_id = x.newcomer_id AND c.status = 'bot_followup'
               ))::int AS bot_only,
              (SELECT COUNT(*) FROM n x WHERE EXISTS (
                 SELECT 1 FROM club_greeter_assignment c
                 WHERE c.newcomer_id = x.newcomer_id AND c.status = 'pending'
               ))::int AS awaiting,
              (SELECT COUNT(*) FROM n x WHERE EXISTS (
                 SELECT 1 FROM club_greeter_assignment c
                 WHERE c.newcomer_id = x.newcomer_id AND c.status = 'reassigned'
               ))::int AS had_reassign
            """,
            CAMPAIGN,
            admin_list,
        )

        entered_no_greeter = len(greeter_no_list)

        admin_in_waves = await conn.fetchval(
            """
            SELECT COUNT(*)::int
            FROM gift_wave_member gwm
            JOIN gift_wave w ON w.id = gwm.wave_id
            WHERE w.campaign = $1 AND gwm.user_id = ANY($2::bigint[])
            """,
            CAMPAIGN,
            admin_list,
        )
    finally:
        await conn.close()

    b1_sent, b1_day = 0, None
    if bib_env.get("DB_USER"):
        bconn = await _connect(bib_env)
        try:
            brow = await bconn.fetchrow(
                """
                SELECT COUNT(*) FILTER (
                         WHERE ma.status = 'sent' AND ma.user_id != ALL($1::bigint[])
                       )::int AS sent,
                       (MIN(ma.updated_at) FILTER (WHERE ma.status = 'sent')
                          AT TIME ZONE 'Europe/Moscow')::date AS day
                FROM mailing_audience ma
                JOIN mailing_campaigns c ON c.id = ma.campaign_id
                WHERE c.name LIKE 'gift-2026-09%'
                """,
                admin_list,
            )
            if brow:
                b1_sent = int(brow["sent"] or 0)
                b1_day = brow["day"]
        finally:
            await bconn.close()

    today = datetime.now(MSK).date()
    free_by_offset: Dict[int, int] = {i: 0 for i in range(0, 8)}
    for r in free_rows:
        fd = r["free_day"]
        if fd is None:
            continue
        if isinstance(fd, datetime):
            fd = fd.date()
        offset = (fd - today).days
        if offset < 0:
            offset = 0
        if offset > 7:
            continue
        free_by_offset[offset] = free_by_offset.get(offset, 0) + int(r["n"] or 0)

    occupied = int((tickets_hold_all or {}).get("occupied") or 0)
    holding = int((tickets_hold_all or {}).get("holding") or 0)
    slots_free = max(0, TICKET_CAP - occupied)

    return {
        "admins_n": len(admins),
        "admin_in_waves": int(admin_in_waves or 0),
        "t1_days": [dict(r) for r in t1_days],
        "b1_sent": b1_sent,
        "b1_day": b1_day,
        "apps_days": [dict(r) for r in apps_days],
        "apps": dict(apps_tot) if apps_tot else {},
        "queue_ops": dict(queue_ops) if queue_ops else {},
        "tickets": {
            "cap": TICKET_CAP,
            "occupied": occupied,
            "holding": holding,
            "free_now": slots_free,
            "ttl_days": ttl_days,
            "free_by_day": [
                {
                    "offset": i,
                    "label": _free_day_label(i, today),
                    "n": int(free_by_offset.get(i) or 0),
                }
                for i in range(0, 8)
            ],
            "free_7d_total": sum(
                int(free_by_offset.get(i) or 0) for i in range(0, 8)
            ),
        },
        "waves": [dict(r) for r in waves],
        "entered": [dict(r) for r in entered],
        "greeter": [dict(r) for r in greeter],
        "greeter_people": dict(greeter_people) if greeter_people else {},
        "greeter_by": [dict(r) for r in greeter_by],
        "greeter_no_list": [dict(r) for r in greeter_no_list],
        "greeter_newcomers": [dict(r) for r in greeter_newcomers],
        "greeter_assignments": [dict(r) for r in greeter_assignments],
        "greeter_pool": [dict(r) for r in greeter_pool],
        "greeter_outcome": dict(greeter_outcome) if greeter_outcome else {},
        "entered_no_greeter": int(entered_no_greeter or 0),
        "generated_at": datetime.now(MSK),
    }


def _free_day_label(offset: int, today: date) -> str:
    d = today + timedelta(days=offset)
    ds = d.strftime("%d.%m")
    if offset == 0:
        return f"сегодня ({ds})"
    if offset == 1:
        return f"завтра ({ds})"
    if offset == 2:
        return f"послезавтра ({ds})"
    return f"через {offset} дн. ({ds})"


def _msg_buckets(entered: Sequence[Dict[str, Any]]) -> List[Tuple[str, int]]:
    buckets = [
        ("0", 0),
        ("1", 0),
        ("2–4", 0),
        ("5–9", 0),
        ("10–15", 0),
        ("более 15", 0),
    ]
    idx = {"0": 0, "1": 1, "2–4": 2, "5–9": 3, "10–15": 4, "более 15": 5}
    counts = [0] * 6
    for e in entered:
        m = int(e["msgs"] or 0)
        if m <= 0:
            counts[0] += 1
        elif m == 1:
            counts[1] += 1
        elif m <= 4:
            counts[2] += 1
        elif m <= 9:
            counts[3] += 1
        elif m <= 15:
            counts[4] += 1
        else:
            counts[5] += 1
    return [(buckets[i][0], counts[i]) for i in range(6)]


def render(data: Dict[str, Any], *, live: bool = True) -> str:
    apps = data["apps"]
    waves = data["waves"]
    entered = data["entered"]
    t1 = data["t1_days"]
    qops = data.get("queue_ops") or {}
    tickets = data.get("tickets") or {}
    submitted = int(apps.get("submitted") or 0)
    won = sum(int(w["won"]) for w in waves)
    entered_n = sum(int(w["entered"]) for w in waves)
    wrote_n = sum(1 for e in entered if int(e["msgs"] or 0) > 0)
    t1_sent = sum(int(r["sent"]) for r in t1)
    t1_blocked = sum(int(r["blocked"]) for r in t1)
    invites = t1_sent + int(data["b1_sent"])
    free_now = int(tickets.get("free_now") or max(0, TICKET_CAP - won))
    holding = int(tickets.get("holding") or 0)
    occupied = int(tickets.get("occupied") or 0)
    waiting_review = int(qops.get("waiting_review") or apps.get("waiting_review") or 0)
    ready_draw = int(qops.get("ready_draw") or apps.get("ready_draw") or 0)
    selected_no_ticket = int(qops.get("selected_no_ticket") or 0)
    wave_queued = int(qops.get("wave_queued") or 0)
    gen_dt = data["generated_at"]
    gen = gen_dt.strftime("%d.%m.%Y %H:%M МСК")
    gen_iso = gen_dt.isoformat(timespec="seconds")

    free_days = tickets.get("free_by_day") or []
    max_free = max((int(x.get("n") or 0) for x in free_days), default=1) or 1
    free_bars = "".join(
        _bar_row(str(x["label"]), int(x.get("n") or 0), max_free)
        for x in free_days
    )
    free_table_rows = "".join(
        f"<tr><td>{html.escape(str(x['label']))}</td>"
        f"<td class='num'>{_fmt(int(x.get('n') or 0))}</td></tr>"
        for x in free_days
    )
    free_7d = int(tickets.get("free_7d_total") or 0)
    ttl_days = int(tickets.get("ttl_days") or 7)

    refresh_ui = ""
    if live:
        refresh_ui = f"""
<div class="snapbar" id="snapbar">
  <div class="snap-meta">
    <span class="snap-lbl">Последний снимок</span>
    <time id="snap-time" datetime="{html.escape(gen_iso)}">{html.escape(gen)}</time>
    <span class="snap-hint">цифры из БД на этот момент; сами не обновляются</span>
  </div>
  <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center">
    <button type="button" class="snap-btn" id="snap-refresh">Обновить снимок</button>
    <button type="button" class="snap-btn snap-btn-draw" id="snap-draw">🎲 Провести розыгрыш</button>
  </div>
  <span class="snap-status" id="snap-status" hidden></span>
</div>
<script>
(function(){{
  var st = document.getElementById('snap-status');
  function setBusy(btn, on) {{
    if (btn) btn.disabled = !!on;
  }}
  var refreshBtn = document.getElementById('snap-refresh');
  if (refreshBtn) refreshBtn.addEventListener('click', async function(){{
    setBusy(refreshBtn, true);
    setBusy(document.getElementById('snap-draw'), true);
    st.hidden = false;
    st.textContent = 'Считаю из базы…';
    try {{
      var r = await fetch('api/refresh', {{
        method: 'POST',
        credentials: 'same-origin',
        headers: {{'Accept': 'application/json'}}
      }});
      var body = await r.json().catch(function(){{ return {{}}; }});
      if (!r.ok) throw new Error(body.detail || body.error || ('HTTP ' + r.status));
      st.textContent = 'Готово, перезагружаю…';
      location.reload();
    }} catch (e) {{
      st.textContent = 'Не удалось обновить: ' + (e && e.message ? e.message : e);
      setBusy(refreshBtn, false);
      setBusy(document.getElementById('snap-draw'), false);
    }}
  }});
  var drawBtn = document.getElementById('snap-draw');
  if (drawBtn) drawBtn.addEventListener('click', async function(){{
    if (!confirm('Запустить розыгрыш / выдачу партии сейчас?')) return;
    setBusy(drawBtn, true);
    setBusy(refreshBtn, true);
    st.hidden = false;
    st.textContent = 'Розыгрыш…';
    try {{
      var r = await fetch('api/draw', {{
        method: 'POST',
        credentials: 'same-origin',
        headers: {{'Accept': 'application/json'}}
      }});
      var body = await r.json().catch(function(){{ return {{}}; }});
      if (!r.ok) throw new Error(body.detail || body.error || ('HTTP ' + r.status));
      st.textContent = (body.message || 'Готово') + ' — обновляю снимок…';
      location.reload();
    }} catch (e) {{
      st.textContent = 'Розыгрыш не удался: ' + (e && e.message ? e.message : e);
      setBusy(drawBtn, false);
      setBusy(refreshBtn, false);
    }}
  }});
}})();
</script>
"""

    # invite by calendar day
    by_day: Dict[str, int] = {}
    for r in t1:
        d = str(r["day"])
        by_day[d] = by_day.get(d, 0) + int(r["sent"])
    if data["b1_day"] and data["b1_sent"]:
        d = str(data["b1_day"])
        by_day[d] = by_day.get(d, 0) + int(data["b1_sent"])
    max_day = max(by_day.values()) if by_day else 1
    invite_bars = "".join(
        _bar_row(
            datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m"),
            v,
            max_day,
        )
        for d, v in sorted(by_day.items())
    )

    max_app = max((int(r["n"]) for r in data["apps_days"]), default=1)
    app_bars = "".join(
        _bar_row(
            r["day"].strftime("%d.%m") if hasattr(r["day"], "strftime") else str(r["day"])[5:10].replace("-", "."),
            int(r["n"]),
            max_app,
        )
        for r in data["apps_days"]
    )

    buckets = _msg_buckets(entered)
    max_b = max((c for _, c in buckets), default=1)
    bucket_bars = "".join(_bar_row(lab, c, max_b) for lab, c in buckets if True)

    ge2 = sum(1 for e in entered if int(e["msgs"] or 0) >= 2)
    ge5 = sum(1 for e in entered if int(e["msgs"] or 0) >= 5)
    ge10 = sum(1 for e in entered if int(e["msgs"] or 0) >= 10)
    gt15 = sum(1 for e in entered if int(e["msgs"] or 0) > 15)

    wave_rows = ""
    for w in waves:
        day = w["day"].strftime("%d.%m") if hasattr(w["day"], "strftime") else str(w["day"])
        wave_rows += (
            f"<tr>"
            f"<td><strong>Волна {int(w['wave_index'])}</strong><div class='sub'>{html.escape(day)}</div></td>"
            f"<td class='num'>{_fmt(int(w['won']))}</td>"
            f"<td class='num'>{int(w['by_score'])} / {int(w['by_draw'])}</td>"
            f"<td class='num'>{_fmt(int(w['entered']))} <span class='muted'>({_pct(int(w['entered']), int(w['won']))})</span></td>"
            f"<td class='num'>{_fmt(int(w['wrote']))}</td>"
            f"<td class='num'>{_fmt(int(w['waiting']))}</td>"
            f"</tr>"
        )

    t1_rows = ""
    for r in t1:
        day = r["day"].strftime("%d.%m.%Y") if hasattr(r["day"], "strftime") else str(r["day"])
        t1_rows += (
            f"<tr><td>{html.escape(day)}</td><td>{html.escape(str(r['cohort'] or '—'))}</td>"
            f"<td class='num'>{_fmt(int(r['sent']))}</td>"
            f"<td class='num'>{_fmt(int(r['blocked']))}</td></tr>"
        )
    if data["b1_sent"]:
        bd = data["b1_day"].strftime("%d.%m.%Y") if data["b1_day"] else "—"
        t1_rows += (
            f"<tr><td>{html.escape(bd)}</td><td>B1 · Библия</td>"
            f"<td class='num'>{_fmt(int(data['b1_sent']))}</td>"
            f"<td class='num'>0</td></tr>"
        )

    def _person(row: Dict[str, Any], *, uname="username", fname="first_name", uid="user_id") -> str:
        u = (row.get(uname) or "").strip()
        f = (row.get(fname) or "").strip()
        if u:
            return f"@{u}"
        if f:
            return f
        return str(row.get(uid) or "—")

    def _fmt_dt(v: Any) -> str:
        if not v:
            return "—"
        if hasattr(v, "strftime"):
            return v.strftime("%d.%m %H:%M")
        return str(v)[:16]

    status_ru = {
        "replied": "ответил",
        "pending": "ждёт",
        "bot_followup": "бот",
        "reassigned": "переназначен",
        "cancelled": "отмена",
    }

    gmap = {r["status"]: int(r["n"]) for r in data["greeter"]}
    greeter_bars = "".join(
        _bar_row(
            status_ru.get(st, st),
            n,
            max(gmap.values()) if gmap else 1,
        )
        for st, n in sorted(gmap.items(), key=lambda x: -x[1])
    )

    greeter_rows = ""
    for g in data["greeter_by"]:
        name = _person(g, uid="greeter_id")
        avg_m = g.get("avg_reply_min")
        avg_s = f"{int(avg_m)} мин" if avg_m is not None else "—"
        replied = int(g["replied"])
        assigned = int(g["assigned"])
        greeter_rows += (
            f"<tr><td>{html.escape(name)}</td>"
            f"<td class='num'>{assigned}</td>"
            f"<td class='num'>{replied}</td>"
            f"<td class='num'>{int(g['pending'])}</td>"
            f"<td class='num'>{int(g['bot_fu'])}</td>"
            f"<td class='num'>{int(g['reassigned'])}</td>"
            f"<td class='num'>{html.escape(avg_s)}</td></tr>"
        )

    go = data.get("greeter_outcome") or {}
    with_assign = int(go.get("with_assign") or 0)
    human_ok = int(go.get("human_ok") or 0)
    bot_only = int(go.get("bot_only") or 0)
    awaiting = int(go.get("awaiting") or 0)
    had_reassign = int(go.get("had_reassign") or 0)

    outcome_bars = "".join(
        _bar_row(lab, n, max(with_assign, 1))
        for lab, n in (
            ("человек ответил", human_ok),
            ("только бот догнал", bot_only),
            ("ещё ждут ответа", awaiting),
            ("был перенос 90 мин", had_reassign),
        )
    )

    no_g_rows = ""
    for r in data.get("greeter_no_list") or []:
        no_g_rows += (
            f"<tr>"
            f"<td>{html.escape(_person(r))}</td>"
            f"<td class='num'>{int(r['wave_index'])}</td>"
            f"<td>{html.escape(str(r['status']))}</td>"
            f"<td class='num'>{html.escape(str(r.get('hours_in_group') if r.get('hours_in_group') is not None else '—'))}</td>"
            f"<td>{html.escape(_fmt_dt(r.get('joined_msk') or r.get('activated_msk')))}</td>"
            f"</tr>"
        )

    nc_rows = ""
    for r in data.get("greeter_newcomers") or []:
        if r.get("got_reply"):
            outcome = "человек ответил"
        elif r.get("still_pending"):
            outcome = "ждёт"
        elif r.get("got_bot"):
            outcome = "бот догнал"
        else:
            outcome = "перенос / обрыв"
        reply_s = (
            f"{int(r['reply_min'])} мин"
            if r.get("reply_min") is not None
            else "—"
        )
        nc_rows += (
            f"<tr>"
            f"<td>{html.escape(_person(r))}</td>"
            f"<td class='num'>{int(r['wave_index'])}</td>"
            f"<td class='num'>{int(r['msgs'] or 0)}</td>"
            f"<td class='num'>{int(r['attempts'] or 0)}</td>"
            f"<td>{html.escape(outcome)}</td>"
            f"<td class='num'>{html.escape(reply_s)}</td>"
            f"<td style='font-size:12.5px;color:var(--ink-soft)'>{html.escape(str(r.get('chain') or '—'))}</td>"
            f"</tr>"
        )

    asg_rows = ""
    for r in data.get("greeter_assignments") or []:
        asg_rows += (
            f"<tr>"
            f"<td class='num'>{int(r['id'])}</td>"
            f"<td>{html.escape(_fmt_dt(r.get('assigned_msk')))}</td>"
            f"<td>{html.escape(_person(r, uname='newcomer_uname', fname='newcomer_fname', uid='newcomer_id'))}</td>"
            f"<td>{html.escape(_person(r, uname='greeter_uname', fname='greeter_fname', uid='greeter_id'))}</td>"
            f"<td class='num'>п{int(r['attempt'])}</td>"
            f"<td>{html.escape(status_ru.get(str(r['status']), str(r['status'])))}</td>"
            f"<td class='num'>{html.escape(str(int(r['waited_min'])) if r.get('waited_min') is not None else '—')} мин</td>"
            f"</tr>"
        )

    pool_rows = ""
    active_pool = 0
    for r in data.get("greeter_pool") or []:
        if r.get("active"):
            active_pool += 1
        paused = r.get("paused_msk")
        pause_s = _fmt_dt(paused) if paused else "—"
        state = "активен" if r.get("active") else "выкл"
        if paused:
            state = "пауза"
        pool_rows += (
            f"<tr>"
            f"<td>{html.escape(_person(r))}</td>"
            f"<td>{html.escape(state)}</td>"
            f"<td class='num'>{int(r['capacity'] or 0)}</td>"
            f"<td class='num'>{int(r['miss_streak'] or 0)}</td>"
            f"<td class='num'>{int(r['open_pending'] or 0)}</td>"
            f"<td class='num'>{int(r['today_n'] or 0)}</td>"
            f"<td>{html.escape(pause_s)}</td>"
            f"</tr>"
        )

    people_rows = ""
    for e in entered:
        label = _person(e)
        people_rows += (
            f"<tr>"
            f"<td>{html.escape(label)}</td>"
            f"<td class='num'>{int(e['wave_index'])}</td>"
            f"<td>{html.escape(str(e['status']))}</td>"
            f"<td class='num'>{int(e['msgs'] or 0)}</td>"
            f"</tr>"
        )

    gp = data["greeter_people"]
    wrote_with_g = with_assign
    silent_no_g = int(data["entered_no_greeter"])
    funnel_max = max(invites, submitted, won, entered_n, wrote_n, 1)

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow,noarchive">
<title>Розыгрыш gift-2026-09 · статистика</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
:root{{--ground:#F4F4F2;--surface:#FFF;--surface-2:#E9E9E5;--ink:#141613;--ink-soft:#54574F;
--ink-faint:#82867B;--accent:#2F5D3A;--accent-soft:#DFE9E0;--hot:#A6431E;--hot-soft:#F6E2D9;
--warn:#7A5B12;--warn-soft:#F1E8CF;--rule:#CBCCC4;--rule-soft:#E1E2DB;
--shadow:0 1px 2px rgba(20,22,19,.06),0 10px 28px -16px rgba(20,22,19,.3)}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--ground:#101210;--surface:#181A17;
--surface-2:#212420;--ink:#E7E9E3;--ink-soft:#9DA396;--ink-faint:#767C6F;--accent:#7FBE8D;
--accent-soft:#1A2C1E;--hot:#E28B62;--hot-soft:#33200F;--warn:#D6B45E;--warn-soft:#2E2711;
--rule:#2C302B;--rule-soft:#232622;--shadow:0 1px 2px rgba(0,0,0,.5),0 10px 28px -16px rgba(0,0,0,.9)}}}}
:root[data-theme="dark"]{{--ground:#101210;--surface:#181A17;--surface-2:#212420;--ink:#E7E9E3;
--ink-soft:#9DA396;--ink-faint:#767C6F;--accent:#7FBE8D;--accent-soft:#1A2C1E;--hot:#E28B62;
--hot-soft:#33200F;--warn:#D6B45E;--warn-soft:#2E2711;--rule:#2C302B;--rule-soft:#232622;
--shadow:0 1px 2px rgba(0,0,0,.5),0 10px 28px -16px rgba(0,0,0,.9)}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--ground);color:var(--ink);font-family:"IBM Plex Sans",-apple-system,sans-serif;font-size:16px;line-height:1.55;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1040px;margin:0 auto;padding:0 24px 90px}}
a{{color:var(--accent)}}
.topnav{{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:center;padding:12px 24px;background:var(--surface-2);border-bottom:1px solid var(--rule);font-family:"IBM Plex Mono",monospace;font-size:12px}}
.topnav a{{color:var(--accent);text-decoration:none}}.topnav a:hover{{text-decoration:underline}}
.topnav .cur{{color:var(--ink-faint)}}.topnav .sep{{color:var(--ink-faint)}}
.mast{{padding:48px 0 22px;border-bottom:3px solid var(--ink);margin-bottom:28px}}
.eyebrow{{font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.15em;text-transform:uppercase;color:var(--ink-faint);margin:0 0 14px}}
h1{{font-size:clamp(28px,4.2vw,42px);font-weight:700;line-height:1.06;letter-spacing:-.022em;margin:0 0 12px}}
.sub{{font-size:17px;color:var(--ink-soft);max-width:64ch;margin:0}}
.sec{{margin-top:46px;padding-top:18px;border-top:2px solid var(--ink)}}
.kick{{font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.15em;text-transform:uppercase;color:var(--accent);margin:0 0 10px}}
h2{{font-size:clamp(20px,2.6vw,26px);font-weight:700;line-height:1.18;letter-spacing:-.015em;margin:0 0 8px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:22px 0 8px}}
.kpi{{background:var(--surface);border:1px solid var(--rule);box-shadow:var(--shadow);padding:16px 18px}}
.kpi .n{{font-family:"IBM Plex Mono",monospace;font-size:clamp(26px,3.5vw,34px);font-weight:600;letter-spacing:-.02em;line-height:1.05}}
.kpi .l{{font-size:13px;color:var(--ink-soft);margin-top:6px}}
.kpi .s{{font-family:"IBM Plex Mono",monospace;font-size:11px;color:var(--ink-faint);margin-top:4px}}
.funnel{{display:grid;gap:8px;margin:18px 0}}
.funnel .step{{display:grid;grid-template-columns:120px 1fr 72px;gap:12px;align-items:center}}
.funnel .lab{{font-size:14px;font-weight:500}}
.funnel .track{{height:28px;background:var(--surface-2);border:1px solid var(--rule-soft);overflow:hidden}}
.funnel .fill{{height:100%;background:var(--accent);opacity:.85}}
.funnel .val{{font-family:"IBM Plex Mono",monospace;font-weight:600;text-align:right}}
.bars{{display:grid;gap:8px;margin:14px 0 6px}}
.bar-row{{display:grid;grid-template-columns:64px 1fr 64px;gap:10px;align-items:center}}
.bar-lab{{font-family:"IBM Plex Mono",monospace;font-size:12.5px;color:var(--ink-soft)}}
.bar-track{{height:18px;background:var(--surface-2);border:1px solid var(--rule-soft)}}
.bar-fill{{height:100%;background:var(--accent);min-width:2px}}
.bar-val{{font-family:"IBM Plex Mono",monospace;font-size:13px;font-weight:600;text-align:right}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:28px}}
@media (max-width:760px){{.grid2{{grid-template-columns:1fr}}.funnel .step{{grid-template-columns:90px 1fr 56px}}}}
.tscroll{{overflow-x:auto;margin:16px 0;border:1px solid var(--rule);background:var(--surface);box-shadow:var(--shadow)}}
table{{border-collapse:collapse;width:100%;font-size:14.5px;min-width:520px}}
th{{text-align:left;font-family:"IBM Plex Mono",monospace;font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-faint);font-weight:500;padding:11px 14px;background:var(--surface-2);border-bottom:1px solid var(--rule);white-space:nowrap}}
td{{padding:11px 14px;border-bottom:1px solid var(--rule-soft);vertical-align:top}}
tbody tr:last-child td{{border-bottom:none}}
.num{{text-align:right;font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums;white-space:nowrap}}
th.num{{text-align:right}}
td .sub{{font-size:12px;color:var(--ink-faint);margin-top:2px}}
.muted{{color:var(--ink-faint);font-weight:500}}
.note{{border-left:3px solid var(--accent);background:var(--accent-soft);padding:14px 18px;margin:18px 0;max-width:74ch}}
.note.warn{{border-left-color:var(--warn);background:var(--warn-soft)}}
.note p{{margin:0;font-size:15px}}.note p+p{{margin-top:8px}}
.note .lbl{{font-family:"IBM Plex Mono",monospace;font-size:10px;letter-spacing:.13em;text-transform:uppercase;display:block;margin-bottom:6px;font-weight:600;color:var(--ink-soft)}}
.chip{{display:inline-block;font-family:"IBM Plex Mono",monospace;font-size:12px;padding:4px 10px;background:var(--surface);border:1px solid var(--rule);margin:0 8px 8px 0}}
.snapbar{{display:flex;flex-wrap:wrap;gap:12px 18px;align-items:center;justify-content:space-between;
  padding:14px 18px;margin:0 0 22px;background:var(--surface);border:1px solid var(--rule);box-shadow:var(--shadow)}}
.snap-meta{{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:baseline}}
.snap-lbl{{font-family:"IBM Plex Mono",monospace;font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink-faint);font-weight:600}}
.snap-meta time{{font-family:"IBM Plex Mono",monospace;font-size:15px;font-weight:600}}
.snap-hint{{font-size:13px;color:var(--ink-soft)}}
.snap-btn{{font-family:"IBM Plex Sans",sans-serif;font-size:14px;font-weight:600;padding:9px 16px;
  border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer}}
.snap-btn:hover{{filter:brightness(1.06)}}
.snap-btn:disabled{{opacity:.55;cursor:wait}}
.snap-btn-draw{{background:var(--hot);border-color:var(--hot)}}
.snap-status{{font-family:"IBM Plex Mono",monospace;font-size:12.5px;color:var(--ink-soft)}}
.foot{{margin-top:50px;padding-top:20px;border-top:2px solid var(--ink);font-size:13px;color:var(--ink-faint)}}
.foot p{{margin:0 0 6px;max-width:none;font-size:13px}}
@media (prefers-reduced-motion:reduce){{*{{animation:none!important;transition:none!important}}}}
</style>
</head>
<body>
<nav class="topnav">
  <a href="../../">← Все материалы</a><span class="sep">·</span>
  <a href="../0284d5fecc/">ТЗ: анкета</a><span class="sep">·</span>
  <a href="../44beec7687/">ТЗ: волна</a><span class="sep">·</span>
  <span class="cur">статистика розыгрыша</span>
</nav>

<div class="wrap">
<header class="mast">
  <p class="eyebrow">Кампания {html.escape(CAMPAIGN)}</p>
  <h1>Розыгрыш: статистика</h1>
  <p class="sub">Приглашения → анкеты → волны → вход в группу → сообщения → встречающие. Админы из всех цифр исключены.</p>
</header>
{refresh_ui}

<section class="sec" style="margin-top:28px;padding-top:0;border-top:none">
  <p class="kick">Очередь и билеты</p>
  <h2>Кто ждёт и когда освободятся слоты</h2>
  <div class="kpis">
    <div class="kpi"><div class="n">{_fmt(waiting_review)}</div><div class="l">анкеты на проверке</div><div class="s">submitted · review</div></div>
    <div class="kpi"><div class="n">{_fmt(ready_draw)}</div><div class="l">готовы к розыгрышу</div><div class="s">queued · pass</div></div>
    <div class="kpi"><div class="n">{_fmt(selected_no_ticket)}</div><div class="l">отобраны, билета нет</div><div class="s">expired / declined / в пуле волны</div></div>
    <div class="kpi"><div class="n">{_fmt(holding)}</div><div class="l">билет на руках</div><div class="s">ещё не вошли · TTL {ttl_days} дн.</div></div>
    <div class="kpi"><div class="n">{_fmt(free_now)}</div><div class="l">свободно слотов сейчас</div><div class="s">занято {_fmt(occupied)} из {TICKET_CAP}</div></div>
  </div>
  <div class="note">
    <span class="lbl">Как читать</span>
    <p><strong>На проверке</strong> — анкета отправлена, ждёт скоринг. <strong>Готовы к розыгрышу</strong> — прошли проверку и ждут кнопку «Провести розыгрыш». <strong>Отобраны, билета нет</strong> — уже выиграли в волне, но билет сгорел / отклонили / ещё в очереди выдачи волны ({_fmt(wave_queued)} в очереди волны).</p>
    <p><strong>Билет на руках</strong> — ссылка у человека, слот занят, пока не войдёт или не истечёт TTL ({ttl_days} дней с выдачи). После истечения слот снова свободен для новой волны.</p>
  </div>
  <div class="grid2">
    <div>
      <h3 style="font-size:17px;font-weight:600;margin:8px 0 10px">Освобождение билетов · 7 дней</h3>
      <div class="bars">{free_bars or '<p style="color:var(--ink-faint)">нет ожидающих билетов</p>'}</div>
      <p style="font-size:13.5px;color:var(--ink-faint);margin-top:8px">
        Сумма за горизонт: <strong>{_fmt(free_7d)}</strong>
        (если никто из держателей не активирует раньше).
      </p>
    </div>
    <div>
      <div class="tscroll" style="margin-top:8px">
        <table>
          <thead><tr><th>Когда</th><th class="num">Слотов</th></tr></thead>
          <tbody>{free_table_rows or '<tr><td colspan="2">нет данных</td></tr>'}</tbody>
        </table>
      </div>
    </div>
  </div>
</section>

<div class="kpis">
  <div class="kpi"><div class="n">{_fmt(invites)}</div><div class="l">приглашений доставлено</div><div class="s">Т1 {_fmt(t1_sent)} + B1 {_fmt(int(data['b1_sent']))}</div></div>
  <div class="kpi"><div class="n">{_fmt(submitted)}</div><div class="l">анкет отправлено</div><div class="s">на проверке {_fmt(waiting_review)} · к розыгрышу {_fmt(ready_draw)}</div></div>
  <div class="kpi"><div class="n">{_fmt(won)}</div><div class="l">выиграли билет</div><div class="s">свободно {_fmt(free_now)} · на руках {_fmt(holding)}</div></div>
  <div class="kpi"><div class="n">{_fmt(entered_n)}</div><div class="l">вошли в группу</div><div class="s">{_pct(entered_n, won)} от выигравших</div></div>
  <div class="kpi"><div class="n">{_fmt(wrote_n)}</div><div class="l">написали в чат</div><div class="s">{_pct(wrote_n, entered_n)} от вошедших</div></div>
</div>

<div class="note">
  <span class="lbl">Как считаем</span>
  <p>Приглашения — факт доставки из <code>mailing_audience</code> (completed), без админов. Анкеты и волны — <code>gift_application</code> / <code>gift_wave_member</code>, кампания <code>{html.escape(CAMPAIGN)}</code>. Сообщения — user-сообщения в клубной группе с момента билета.</p>
  {"<p>В волне 1 ещё лежат " + str(data["admin_in_waves"]) + " админских билета (до отсечения) — в цифрах страницы их нет.</p>" if data["admin_in_waves"] else ""}
</div>

<section class="sec">
  <p class="kick">Воронка</p>
  <h2>От приглашения до голоса в чате</h2>
  <div class="funnel">
    <div class="step"><div class="lab">Пригласили</div><div class="track"><div class="fill" style="width:{max(4, int(100*invites/funnel_max))}%"></div></div><div class="val">{_fmt(invites)}</div></div>
    <div class="step"><div class="lab">Анкеты</div><div class="track"><div class="fill" style="width:{max(4, int(100*submitted/funnel_max))}%"></div></div><div class="val">{_fmt(submitted)}</div></div>
    <div class="step"><div class="lab">Выиграли</div><div class="track"><div class="fill" style="width:{max(4, int(100*won/funnel_max))}%"></div></div><div class="val">{_fmt(won)}</div></div>
    <div class="step"><div class="lab">Вошли</div><div class="track"><div class="fill" style="width:{max(4, int(100*entered_n/funnel_max))}%"></div></div><div class="val">{_fmt(entered_n)}</div></div>
    <div class="step"><div class="lab">Написали</div><div class="track"><div class="fill" style="width:{max(4, int(100*wrote_n/funnel_max))}%"></div></div><div class="val">{_fmt(wrote_n)}</div></div>
  </div>
  <p style="color:var(--ink-soft);font-size:14px;margin-top:8px">Конверсия приглашение→анкета {_pct(submitted, invites)} · анкета→билет {_pct(won, submitted)} · билет→вход {_pct(entered_n, won)} · вход→сообщение {_pct(wrote_n, entered_n)}</p>
</section>

<section class="sec">
  <p class="kick">1 · Приглашения</p>
  <h2>Сколько отправили, по датам</h2>
  <div class="grid2">
    <div>
      <div class="bars">{invite_bars}</div>
      <p style="font-size:13.5px;color:var(--ink-faint)">Сумма по календарным дням (Т1 клуб + Т2 Библия B1).</p>
    </div>
    <div>
      <div class="chip">доставлено {_fmt(invites)}</div>
      <div class="chip">блок Т1 {_fmt(t1_blocked)}</div>
      <div class="tscroll" style="margin-top:12px">
        <table>
          <thead><tr><th>Дата</th><th>Когорта</th><th class="num">Доставлено</th><th class="num">Блок</th></tr></thead>
          <tbody>{t1_rows}</tbody>
        </table>
      </div>
    </div>
  </div>
</section>

<section class="sec">
  <p class="kick">2 · Анкеты</p>
  <h2>Сколько заполнили</h2>
  <div class="grid2">
    <div>
      <div class="bars">{app_bars}</div>
    </div>
    <div>
      <div class="kpi" style="margin-bottom:12px"><div class="n">{_fmt(submitted)}</div><div class="l">отправлено</div>
        <div class="s">черновики {_fmt(int(apps.get('draft') or 0))} · expired {_fmt(int(apps.get('expired') or 0))} · отмены {_fmt(int(apps.get('cancelled') or 0))}</div>
      </div>
      <p style="font-size:14.5px;color:var(--ink-soft);margin:0">На проверке: <strong>{_fmt(waiting_review)}</strong> · готовы к розыгрышу: <strong>{_fmt(ready_draw)}</strong> · отобраны без билета: <strong>{_fmt(selected_no_ticket)}</strong></p>
    </div>
  </div>
</section>

<section class="sec">
  <p class="kick">3–4 · Розыгрыши и вход</p>
  <h2>По каждой волне</h2>
  <div class="tscroll">
    <table>
      <thead>
        <tr>
          <th>Волна</th>
          <th class="num">Выиграли</th>
          <th class="num">Баллы / жребий</th>
          <th class="num">Вошли</th>
          <th class="num">Написали</th>
          <th class="num">Ещё не активировали</th>
        </tr>
      </thead>
      <tbody>
        {wave_rows}
        <tr>
          <td><strong>Итого</strong></td>
          <td class="num"><strong>{_fmt(won)}</strong></td>
          <td class="num">{sum(int(w['by_score']) for w in waves)} / {sum(int(w['by_draw']) for w in waves)}</td>
          <td class="num"><strong>{_fmt(entered_n)}</strong></td>
          <td class="num"><strong>{_fmt(sum(int(w['wrote']) for w in waves))}</strong></td>
          <td class="num">{_fmt(sum(int(w['waiting']) for w in waves))}</td>
        </tr>
      </tbody>
    </table>
  </div>
</section>

<section class="sec">
  <p class="kick">5 · Активность в группе</p>
  <h2>Разбивка по числу сообщений</h2>
  <p style="color:var(--ink-soft);font-size:15px;max-width:64ch;margin:0 0 8px">Среди {_fmt(entered_n)} вошедших. Корзины: 0 · 1 · 2–4 · 5–9 · 10–15 · более 15.</p>
  <div class="grid2">
    <div>
      <div class="bars">{bucket_bars}</div>
      <p style="font-size:13.5px;color:var(--ink-faint);margin-top:10px">
        Пороги: ≥2 → <strong>{ge2}</strong> · ≥5 → <strong>{ge5}</strong> · ≥10 → <strong>{ge10}</strong> · &gt;15 → <strong>{gt15}</strong>
      </p>
    </div>
    <div>
      <div class="tscroll">
        <table>
          <thead><tr><th>Кто</th><th class="num">Волна</th><th>Статус</th><th class="num">Сообщ.</th></tr></thead>
          <tbody>{people_rows}</tbody>
        </table>
      </div>
    </div>
  </div>
</section>

<section class="sec">
  <p class="kick">6 · Встречающие</p>
  <h2>Как встречают новичков розыгрыша</h2>

  <div class="note">
    <span class="lbl">Правило</span>
    <p>Встречающий назначается <strong>при первом сообщении</strong> новичка в клубной группе — не в момент входа. Поэтому молчаливые вошедшие без назначения — ожидаемо. Через 90 минут без ответа — перенос другому; через ~180 минут — follow-up бота.</p>
  </div>

  <div class="kpis">
    <div class="kpi"><div class="n">{_fmt(entered_n)}</div><div class="l">вошли в группу</div><div class="s">из розыгрыша</div></div>
    <div class="kpi"><div class="n">{_fmt(wrote_n)}</div><div class="l">написали в чат</div><div class="s">триггер назначения</div></div>
    <div class="kpi"><div class="n">{_fmt(wrote_with_g)}</div><div class="l">получили встречающего</div><div class="s">{_pct(wrote_with_g, wrote_n)} от написавших</div></div>
    <div class="kpi"><div class="n">{_fmt(silent_no_g)}</div><div class="l">вошли, молчат</div><div class="s">назначения нет</div></div>
    <div class="kpi"><div class="n">{_fmt(human_ok)}</div><div class="l">живой ответ</div><div class="s">{_pct(human_ok, wrote_with_g)} от назначенных</div></div>
    <div class="kpi"><div class="n">{html.escape(str(gp.get('avg_reply_h') or '—'))}</div><div class="l">ср. ответ, часы</div><div class="s">только где replied</div></div>
  </div>

  <h3 style="font-size:18px;font-weight:600;margin:28px 0 8px;padding-bottom:6px;border-bottom:1px solid var(--rule-soft)">Исход по новичкам (кому назначали)</h3>
  <div class="grid2">
    <div>
      <div class="bars">{outcome_bars}</div>
      <p style="font-size:13.5px;color:var(--ink-faint);margin-top:8px">
        Назначений всего: <strong>{_fmt(int(gp.get('assignments') or 0))}</strong>
        (попыток, включая переносы) на <strong>{_fmt(wrote_with_g)}</strong> человек.
        Пул активных встречающих: <strong>{_fmt(active_pool)}</strong>.
      </p>
    </div>
    <div>
      <div class="bars">{greeter_bars or '<p class="muted">нет назначений</p>'}</div>
      <p style="font-size:13px;color:var(--ink-faint)">Статусы строк назначений (одна попытка = одна строка).</p>
    </div>
  </div>

  <h3 style="font-size:18px;font-weight:600;margin:28px 0 8px;padding-bottom:6px;border-bottom:1px solid var(--rule-soft)">Вошедшие без назначения (молчат в группе)</h3>
  <div class="tscroll">
    <table>
      <thead>
        <tr>
          <th>Кто</th>
          <th class="num">Волна</th>
          <th>Статус</th>
          <th class="num">Часов в группе</th>
          <th>Вход</th>
        </tr>
      </thead>
      <tbody>{no_g_rows or '<tr><td colspan="5">все вошедшие либо написали, либо ещё не зашли</td></tr>'}</tbody>
    </table>
  </div>

  <h3 style="font-size:18px;font-weight:600;margin:28px 0 8px;padding-bottom:6px;border-bottom:1px solid var(--rule-soft)">Путь каждого новичка с встречающим</h3>
  <div class="tscroll">
    <table>
      <thead>
        <tr>
          <th>Новичок</th>
          <th class="num">Волна</th>
          <th class="num">Сообщ.</th>
          <th class="num">Попыток</th>
          <th>Итог</th>
          <th class="num">Ответ за</th>
          <th>Цепочка (кто · статус · попытка)</th>
        </tr>
      </thead>
      <tbody>{nc_rows or '<tr><td colspan="7">пока никому не назначали</td></tr>'}</tbody>
    </table>
  </div>

  <h3 style="font-size:18px;font-weight:600;margin:28px 0 8px;padding-bottom:6px;border-bottom:1px solid var(--rule-soft)">По встречающим</h3>
  <div class="tscroll">
    <table>
      <thead>
        <tr>
          <th>Встречающий</th>
          <th class="num">Назначено</th>
          <th class="num">Ответил</th>
          <th class="num">Ждёт</th>
          <th class="num">Бот</th>
          <th class="num">Переназн.</th>
          <th class="num">Ср. ответ</th>
        </tr>
      </thead>
      <tbody>{greeter_rows or '<tr><td colspan="7">нет данных</td></tr>'}</tbody>
    </table>
  </div>

  <h3 style="font-size:18px;font-weight:600;margin:28px 0 8px;padding-bottom:6px;border-bottom:1px solid var(--rule-soft)">Пул встречающих</h3>
  <div class="tscroll">
    <table>
      <thead>
        <tr>
          <th>Кто</th>
          <th>Состояние</th>
          <th class="num">Ёмкость</th>
          <th class="num">Пропуски</th>
          <th class="num">Открытых</th>
          <th class="num">Сегодня</th>
          <th>Пауза до</th>
        </tr>
      </thead>
      <tbody>{pool_rows or '<tr><td colspan="7">пул пуст</td></tr>'}</tbody>
    </table>
  </div>

  <h3 style="font-size:18px;font-weight:600;margin:28px 0 8px;padding-bottom:6px;border-bottom:1px solid var(--rule-soft)">Все назначения (лента)</h3>
  <div class="tscroll">
    <table>
      <thead>
        <tr>
          <th class="num">id</th>
          <th>Когда</th>
          <th>Новичок</th>
          <th>Встречающий</th>
          <th class="num">Попытка</th>
          <th>Статус</th>
          <th class="num">Ждали</th>
        </tr>
      </thead>
      <tbody>{asg_rows or '<tr><td colspan="7">нет</td></tr>'}</tbody>
    </table>
  </div>
</section>

<footer class="foot">
  <p>Данные из боевых <code>club_db</code> и <code>biblia_bot</code>. Последний снимок: <strong>{html.escape(gen)}</strong>.</p>
  <p>«Снимок» — страница с цифрами на конкретный момент. Кнопка сверху пересобирает отчёт из базы заново.</p>
  <p><a href="../0284d5fecc/">ТЗ анкеты</a> · <a href="../44beec7687/">ТЗ волны</a> · <a href="../../">все материалы</a></p>
</footer>
</div>
</body>
</html>
"""


async def amain() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    data = await collect()
    html_out = render(data, live=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html_out, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    import asyncio
    import sys

    sys.exit(asyncio.run(amain()))
