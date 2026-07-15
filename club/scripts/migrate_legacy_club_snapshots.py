#!/usr/bin/env python3
"""
One-time migration of legacy admin snapshots into club_report_snapshots.

Required env:
- LEGACY_ADMIN_DB_URL
- DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD (target club DB)

Optional:
- LEGACY_SNAPSHOT_BOT_NAME (default club) — filter public.club_snapshots when it has bot_name
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env", interpolate=False)


def _target_db_url() -> str:
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "")
    user = os.getenv("DB_USER", "")
    password = os.getenv("DB_PASSWORD", "")
    if not (name and user):
        raise RuntimeError("DB_NAME/DB_USER are required")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def _legacy_metrics_json_sql(cols: set[str]) -> str:
    """Target column metrics_json from legacy metrics_json or tariff JSON columns."""
    if "metrics_json" in cols:
        return "COALESCE(metrics_json, '{}'::jsonb)"
    parts: list[str] = []
    if "tariff_breakdown" in cols:
        parts.append("'tariff_breakdown', COALESCE(tariff_breakdown, '{}'::jsonb)")
    if "month_tariff_breakdown" in cols:
        parts.append(
            "'month_tariff_breakdown', COALESCE(month_tariff_breakdown, '{}'::jsonb)"
        )
    if parts:
        return f"jsonb_build_object({', '.join(parts)})"
    return "'{}'::jsonb"


def _club_snapshots_select_sql(cols: set[str]) -> str:
    """Build SELECT for legacy club_snapshots; missing columns default to 0 / empty."""

    def n(name: str) -> str:
        if name in cols:
            return name
        raise RuntimeError(
            f"legacy club_snapshots has no column {name!r} (need at least snapshot_date)"
        )

    def i(name: str) -> str:
        if name in cols:
            return f"COALESCE({name}, 0) AS {name}"
        return f"0 AS {name}"

    def f(name: str) -> str:
        if name in cols:
            return f"COALESCE({name}, 0) AS {name}"
        return f"0::double precision AS {name}"

    def t(name: str) -> str:
        if name in cols:
            return f"COALESCE({name}, '') AS {name}"
        return "'' AS report_html"

    # snapshot_date required
    sd = f"{n('snapshot_date')}::date AS snapshot_date"
    metrics = _legacy_metrics_json_sql(cols)
    bot_literal = (os.getenv("LEGACY_SNAPSHOT_BOT_NAME") or "club").replace("'", "''")
    where_bot = ""
    if "bot_name" in cols:
        where_bot = f"\n            WHERE bot_name = '{bot_literal}'"
    return f"""
            SELECT
                {sd},
                {i("total_users")},
                {i("active_users")},
                {i("new_users")},
                {i("pending_orders")},
                {i("pending_unique_users")},
                {i("paid_orders")},
                {i("paid_unique_users")},
                {f("total_amount")},
                {i("month_paid_orders")},
                {i("month_unique_users")},
                {f("month_total_amount")},
                {i("active_licenses")},
                {i("users_expired")},
                {metrics} AS metrics_json,
                {t("report_html")}
            FROM club_snapshots{where_bot}
            ORDER BY snapshot_date
            """


async def _load_legacy_rows(conn: asyncpg.Connection):
    tables = await conn.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema='public'
          AND table_name IN ('club_snapshots', 'metric_snapshots')
        """
    )
    names = {r["table_name"] for r in tables}
    if "club_snapshots" in names:
        col_rows = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'club_snapshots'
            """
        )
        legacy_cols = {r["column_name"] for r in col_rows}
        sql = _club_snapshots_select_sql(legacy_cols)
        return await conn.fetch(sql)
    if "metric_snapshots" in names:
        return await conn.fetch(
            """
            SELECT
                snapshot_date::date AS snapshot_date,
                COALESCE(subscribers, 0) AS total_users,
                COALESCE(dau, 0) AS active_users,
                COALESCE(new_users, 0) AS new_users,
                0 AS pending_orders,
                0 AS pending_unique_users,
                0 AS paid_orders,
                0 AS paid_unique_users,
                COALESCE(donations_amount, 0) AS total_amount,
                0 AS month_paid_orders,
                0 AS month_unique_users,
                COALESCE(donations_month_to_date, 0) AS month_total_amount,
                0 AS active_licenses,
                0 AS users_expired,
                '{}'::jsonb AS metrics_json,
                '' AS report_html
            FROM metric_snapshots
            WHERE bot_name='club'
            ORDER BY snapshot_date
            """
        )
    return []


async def _legacy_empty_hint(conn: asyncpg.Connection) -> str:
    exists = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'club_snapshots'
        )
        """
    )
    if not exists:
        return ""
    total = await conn.fetchval("SELECT count(*)::bigint FROM public.club_snapshots")
    lines = [f"public.club_snapshots has {total} row(s) total."]
    has_bot = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'club_snapshots'
              AND column_name = 'bot_name'
        )
        """
    )
    if has_bot and total:
        dist = await conn.fetch(
            "SELECT bot_name, count(*)::bigint AS c FROM public.club_snapshots GROUP BY 1 ORDER BY 2 DESC"
        )
        lines.append(
            "By bot_name: "
            + ", ".join(f"{r['bot_name']!r}={r['c']}" for r in dist)
            + f" (filter LEGACY_SNAPSHOT_BOT_NAME={(os.getenv('LEGACY_SNAPSHOT_BOT_NAME') or 'club')!r})"
        )
    return "\n".join(lines)


async def main() -> int:
    legacy_url = (os.getenv("LEGACY_ADMIN_DB_URL") or "").strip()
    if not legacy_url:
        print("LEGACY_ADMIN_DB_URL is required", file=sys.stderr)
        return 2

    src = await asyncpg.connect(legacy_url)
    dst = await asyncpg.connect(_target_db_url())
    moved = 0
    try:
        rows = await _load_legacy_rows(src)
        if not rows:
            print("No legacy rows found.")
            hint = await _legacy_empty_hint(src)
            if hint:
                print(hint, file=sys.stderr)
            return 0
        for r in rows:
            await dst.execute(
                """
                INSERT INTO club_report_snapshots (
                    snapshot_date, total_users, active_users, new_users,
                    pending_orders, pending_unique_users,
                    paid_orders, paid_unique_users, total_amount,
                    month_paid_orders, month_unique_users, month_total_amount,
                    active_licenses, users_expired,
                    report_html, metrics_json, source
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17
                )
                ON CONFLICT (snapshot_date) DO UPDATE SET
                    total_users = EXCLUDED.total_users,
                    active_users = EXCLUDED.active_users,
                    new_users = EXCLUDED.new_users,
                    pending_orders = EXCLUDED.pending_orders,
                    pending_unique_users = EXCLUDED.pending_unique_users,
                    paid_orders = EXCLUDED.paid_orders,
                    paid_unique_users = EXCLUDED.paid_unique_users,
                    total_amount = EXCLUDED.total_amount,
                    month_paid_orders = EXCLUDED.month_paid_orders,
                    month_unique_users = EXCLUDED.month_unique_users,
                    month_total_amount = EXCLUDED.month_total_amount,
                    active_licenses = EXCLUDED.active_licenses,
                    users_expired = EXCLUDED.users_expired,
                    report_html = EXCLUDED.report_html,
                    metrics_json = EXCLUDED.metrics_json,
                    source = EXCLUDED.source,
                    updated_at = NOW()
                """,
                r["snapshot_date"],
                int(r["total_users"] or 0),
                int(r["active_users"] or 0),
                int(r["new_users"] or 0),
                int(r["pending_orders"] or 0),
                int(r["pending_unique_users"] or 0),
                int(r["paid_orders"] or 0),
                int(r["paid_unique_users"] or 0),
                float(r["total_amount"] or 0),
                int(r["month_paid_orders"] or 0),
                int(r["month_unique_users"] or 0),
                float(r["month_total_amount"] or 0),
                int(r["active_licenses"] or 0),
                int(r["users_expired"] or 0),
                str(r["report_html"] or ""),
                json.dumps(dict(r["metrics_json"] or {}), ensure_ascii=False),
                "legacy_migration_script",
            )
            moved += 1
    finally:
        await src.close()
        await dst.close()

    print(f"Done. Migrated rows: {moved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
