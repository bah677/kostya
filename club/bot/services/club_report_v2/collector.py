"""Ежедневный отчёт v2: клиенты+финансы / лиды+кампании."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from zoneinfo import ZoneInfo

from bot.services.club_report_collect import ClubReportDailyCollector
from bot.services.followup_leads_report import collect_followup_leads_report
from bot.services.benefit3_deeplink_report import collect_benefit3_deeplink_report
from bot.services.biblia_club_campaign_report import (
    biblia_db_configured,
    collect_biblia_club_campaign_report,
    create_biblia_pool,
)
from bot.services.club_report_v2.deepseek_blocks import (
    analyze_group_day,
    analyze_lead_dialogs,
    format_dialogs_for_llm,
)
from bot.services.report_exclude import sql_exclude_users
from config import config

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")


class ClubReportV2Collector:
    def __init__(
        self,
        pool: "asyncpg.Pool",
        *,
        club_group_id: int = 0,
        user_storage=None,
    ) -> None:
        self._pool = pool
        self._club_group_id = club_group_id
        self._legacy = ClubReportDailyCollector(pool, club_group_id=club_group_id)
        self._storage = user_storage

    async def _scalar(self, query: str, *args: Any) -> Any:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def _all(self, query: str, *args: Any) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
            return [dict(r) for r in rows]

    def _ex(self, col: str, n: int = 1) -> tuple[str, list]:
        return sql_exclude_users(col, start_param=n)

    async def collect_all(self, *, include_llm: bool = True) -> Dict[str, Any]:
        now_msk = datetime.now(MSK)
        yesterday = (now_msk - timedelta(days=1)).date()
        ex_l, ex_ids = self._ex("l.user_id", 1)

        metrics: Dict[str, Any] = {
            "report_version": 2,
            "period": f"за {yesterday.strftime('%d.%m.%Y')} (МСК)",
            "generated_at": now_msk.isoformat(),
        }

        metrics["active_licenses"] = await self._scalar(
            f"""
            SELECT COUNT(DISTINCT l.user_id)
            FROM license l
            WHERE l.status = 'active' AND l.expires_at > NOW()
            {ex_l}
            """,
            *ex_ids,
        ) or 0

        if self._club_group_id:
            ex_g, ex_g_ids = self._ex("m.user_id", 2)
            metrics["club_group_active_yesterday"] = await self._scalar(
                f"""
                SELECT COUNT(DISTINCT m.user_id)
                FROM messages m
                WHERE m.chat_id = $1
                  AND m.role = 'user'
                  AND (m.created_at AT TIME ZONE 'Europe/Moscow')::date
                      = (NOW() AT TIME ZONE 'Europe/Moscow')::date - 1
                  {ex_g}
                """,
                self._club_group_id,
                *ex_g_ids,
            ) or 0

            ex_g2, ex_g2_ids = self._ex("m.user_id", 2)
            ex_l2, ex_l2_ids = self._ex("l.user_id", 2 + len(ex_g2_ids))
            metrics["group_silent_count"] = await self._scalar(
                f"""
                WITH grp AS (
                    SELECT m.user_id,
                           MIN((m.created_at AT TIME ZONE 'Europe/Moscow')::date) AS first_day
                    FROM messages m
                    WHERE m.chat_id = $1 AND m.role = 'user'
                      {ex_g2}
                    GROUP BY m.user_id
                ),
                active AS (
                    SELECT DISTINCT l.user_id
                    FROM license l
                    WHERE l.status = 'active' AND l.expires_at > NOW()
                      {ex_l2}
                )
                SELECT COUNT(*)
                FROM grp g
                JOIN active a ON a.user_id = g.user_id
                WHERE NOT EXISTS (
                    SELECT 1 FROM messages m2
                    WHERE m2.chat_id = $1 AND m2.role = 'user' AND m2.user_id = g.user_id
                      AND (m2.created_at AT TIME ZONE 'Europe/Moscow')::date > g.first_day
                )
                """,
                self._club_group_id,
                *ex_g2_ids,
                *ex_l2_ids,
            ) or 0

        if self._club_group_id:
            metrics["expiring_risk_summary"] = await self._expiring_risk_summary(
                self._club_group_id
            )
        else:
            metrics["expiring_risk_summary"] = {}

        metrics["expired_yesterday"] = await self._scalar(
            f"""
            SELECT COUNT(DISTINCT l.user_id)
            FROM license l
            WHERE l.status = 'expired'
              AND (l.expires_at AT TIME ZONE 'Europe/Moscow')::date
                  = (NOW() AT TIME ZONE 'Europe/Moscow')::date - 1
            {ex_l}
            """,
            *ex_ids,
        ) or 0

        metrics["audience_summary"] = await self._audience_summary()
        metrics["funnel_72h"] = await self._funnel_72h()
        metrics["funnel_72h_new_users"] = await self._funnel_72h_new_users()

        metrics["ai_agent"] = await self._ai_agent_stats()

        metrics["campaigns_by_ref"] = await self._campaign_funnel()
        metrics["campaigns_by_channel"] = await self._channel_funnel()
        metrics["channel_overlap"] = await self._channel_overlap()

        metrics["comparisons"] = await self._load_comparisons()

        legacy = await self._legacy.get_all_metrics()
        for k in (
            "paid_orders",
            "paid_breakdown",
            "pending_orders",
            "month_paid_orders",
            "month_total_amount",
            "tariff_breakdown",
            "tariff_breakdown_30d",
            "tariff_breakdown_all",
            "month_tariff_breakdown",
            "users_expiring",
            "users_expired",
            "monthly_revenue",
            "total_revenue",
            "total_users",
            "active_users",
            "new_users",
        ):
            metrics[k] = legacy.get(k)

        metrics["monthly_revenue"] = await self._legacy.get_monthly_revenue_paced()

        try:
            metrics["followup_leads"] = await collect_followup_leads_report(self._pool)
        except Exception:
            logger.exception("followup_leads slice for daily report failed")
            metrics["followup_leads"] = None

        try:
            metrics["benefit3_deeplink"] = await collect_benefit3_deeplink_report(
                self._pool
            )
        except Exception:
            logger.exception("benefit3_deeplink stats for daily report failed")
            metrics["benefit3_deeplink"] = None

        metrics["biblia_club_campaigns"] = None
        if biblia_db_configured(config):
            biblia_pool = None
            try:
                biblia_pool = await create_biblia_pool(config)
                bot_username = (config.TELEGRAM_BOT_USERNAME or "Talk_God_Bot").lstrip(
                    "@"
                )
                metrics["biblia_club_campaigns"] = (
                    await collect_biblia_club_campaign_report(
                        self._pool,
                        biblia_pool,
                        bot_username=bot_username,
                    )
                )
            except Exception:
                logger.exception("biblia_club_campaign report for daily failed")
            finally:
                if biblia_pool is not None:
                    await biblia_pool.close()

        try:
            if self._storage:
                report_day = yesterday
                metrics["legacy_103_reactivation"] = (
                    await self._storage.collect_legacy_103_reactivation_stats(
                        report_day=report_day
                    )
                )
        except Exception:
            logger.exception("legacy_103_reactivation stats for daily report failed")
            metrics["legacy_103_reactivation"] = None

        if include_llm and config.DEEPSEEK_API_KEY:
            metrics["llm"] = await self._run_llm_blocks(metrics)
        else:
            metrics["llm"] = {}

        return metrics

    async def _audience_summary(self) -> Dict[str, Any]:
        """Сводка по базе users (lifetime): активность бота и клиенты."""
        ex, ids = self._ex("u.user_id", 1)
        row = await self._fetch_row(
            f"""
            WITH base AS (
                SELECT
                    u.user_id,
                    COALESCE(u.is_active, TRUE) AS is_active,
                    EXISTS (
                        SELECT 1 FROM license l
                        WHERE l.user_id = u.user_id
                          AND l.status = 'active'
                          AND l.expires_at > NOW()
                    ) AS is_client
                FROM users u
                WHERE TRUE
                {ex}
            )
            SELECT
                COUNT(*)::int AS total,
                COUNT(*) FILTER (WHERE is_active)::int AS active,
                COUNT(*) FILTER (WHERE is_active AND is_client)::int AS active_clients,
                COUNT(*) FILTER (WHERE is_active AND NOT is_client)::int AS active_leads,
                COUNT(*) FILTER (WHERE NOT is_active)::int AS blocked
            FROM base
            """,
            *ids,
        )
        return dict(row) if row else {}

    @staticmethod
    def _funnel_metrics_from_row(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        r = row or {}
        s = int(r.get("starts") or 0)
        lic = int(r.get("starts_with_active_license") or 0)
        a = int(r.get("ai_dialogs") or 0)
        o = int(r.get("orders") or 0)
        p = int(r.get("paid") or 0)

        def pct(a_: int, b_: int) -> str:
            if b_ == 0:
                return "—"
            return f"{100.0 * a_ / b_:.1f}%"

        return {
            "starts": s,
            "starts_with_active_license": lic,
            "starts_without_active_license": max(0, s - lic),
            "ai_dialogs": a,
            "orders": o,
            "paid": p,
            "cr_ai": pct(a, s),
            "cr_order": pct(o, a),
            "cr_paid": pct(p, o),
            "cr_total": pct(p, s),
        }

    def _funnel_72h_tail_sql(self) -> str:
        return """
            ai_dialog AS (
                SELECT DISTINCT s.user_id
                FROM starters s
                WHERE EXISTS (
                    SELECT 1 FROM messages m
                    WHERE m.user_id = s.user_id
                      AND m.chat_id > 0
                      AND m.role = 'user'
                      AND m.created_at >= s.started_at
                      AND COALESCE(m.content, '') <> ''
                      AND LEFT(TRIM(COALESCE(m.content, '')), 1) <> '/'
                )
            ),
            ordered AS (
                SELECT DISTINCT s.user_id
                FROM starters s
                JOIN orders o ON o.user_id = s.user_id
                  AND o.created_at >= s.started_at
            ),
            paid AS (
                SELECT DISTINCT s.user_id
                FROM starters s
                JOIN orders o ON o.user_id = s.user_id
                  AND o.created_at >= s.started_at
                JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
                WHERE o.status = 'paid'
                  AND o.paid_at >= s.started_at
            )
            SELECT
                (SELECT COUNT(*) FROM starters) AS starts,
                (SELECT COUNT(*) FROM starters s
                 WHERE EXISTS (
                     SELECT 1 FROM license l
                     WHERE l.user_id = s.user_id
                       AND l.status = 'active'
                       AND l.expires_at > s.started_at
                 )) AS starts_with_active_license,
                (SELECT COUNT(*) FROM ai_dialog) AS ai_dialogs,
                (SELECT COUNT(*) FROM ordered) AS orders,
                (SELECT COUNT(*) FROM paid) AS paid
        """

    async def _funnel_72h(self) -> Dict[str, Any]:
        """Воронка по всем /start за 72ч (включая действующих и бывших клиентов)."""
        ex, ids = self._ex("il.user_id", 1)
        row = await self._fetch_row(
            f"""
            WITH window_start AS (
                SELECT NOW() - INTERVAL '72 hours' AS ws
            ),
            raw_starts AS (
                SELECT il.user_id, il.created_at
                FROM interaction_logs il, window_start w
                WHERE il.created_at >= w.ws
                  AND il.event_category = 'message'
                  AND COALESCE(il.data->>'text', '') ILIKE '/start%'
                  {ex}
            ),
            starters AS (
                SELECT DISTINCT ON (user_id) user_id, created_at AS started_at
                FROM raw_starts
                ORDER BY user_id, created_at ASC
            ),
            {self._funnel_72h_tail_sql()}
            """,
            *ids,
        )
        return self._funnel_metrics_from_row(row)

    async def _funnel_72h_new_users(self) -> Dict[str, Any]:
        """Воронка для тех, у кого первый /start в жизни был за последние 72 часа."""
        ex, ids = self._ex("il.user_id", 1)
        row = await self._fetch_row(
            f"""
            WITH window_start AS (
                SELECT NOW() - INTERVAL '72 hours' AS ws
            ),
            first_starts AS (
                SELECT il.user_id, MIN(il.created_at) AS first_ever_at
                FROM interaction_logs il
                WHERE il.event_category = 'message'
                  AND COALESCE(il.data->>'text', '') ILIKE '/start%'
                  {ex}
                GROUP BY il.user_id
            ),
            starters AS (
                SELECT fs.user_id, fs.first_ever_at AS started_at
                FROM first_starts fs, window_start w
                WHERE fs.first_ever_at >= w.ws
            ),
            {self._funnel_72h_tail_sql()}
            """,
            *ids,
        )
        return self._funnel_metrics_from_row(row)

    async def _ai_agent_stats(self) -> Dict[str, Any]:
        ex, ids = self._ex("o.user_id", 1)
        depth = await self._all(
            f"""
            WITH first_pay AS (
                SELECT DISTINCT ON (o.user_id) o.user_id, o.paid_at
                FROM orders o
                JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
                WHERE o.status = 'paid'
                ORDER BY o.user_id, o.paid_at ASC
            ),
            msgs_before AS (
                SELECT fp.user_id,
                    COUNT(*) FILTER (WHERE m.role = 'user') AS user_msgs
                FROM first_pay fp
                JOIN messages m ON m.user_id = fp.user_id
                    AND m.chat_id > 0
                    AND m.created_at <= fp.paid_at
                GROUP BY fp.user_id
            )
            SELECT
                COUNT(*) AS payers,
                COALESCE(AVG(user_msgs), 0) AS avg_user_msgs,
                COALESCE(percentile_cont(0.5) WITHIN GROUP (ORDER BY user_msgs), 0) AS median_user_msgs
            FROM msgs_before
            """,
        )
        d = depth[0] if depth else {}
        return {
            "avg_user_msgs_before_pay": float(d.get("avg_user_msgs") or 0),
            "median_user_msgs_before_pay": float(d.get("median_user_msgs") or 0),
            "payers_with_dm": int(d.get("payers") or 0),
        }

    async def _campaign_funnel(self) -> List[Dict[str, Any]]:
        ex, ids = self._ex("at.user_id", 1)
        return await self._all(
            f"""
            WITH touches AS (
                SELECT at.user_id, at.ref_key, at.touch_key, at.created_at
                FROM attribution_touches at
                WHERE at.ref_key IS NOT NULL
                {ex}
            ),
            entered AS (
                SELECT ref_key, COUNT(DISTINCT user_id) AS cnt FROM touches GROUP BY ref_key
            ),
            ai_u AS (
                SELECT t.ref_key, COUNT(DISTINCT t.user_id) AS cnt
                FROM touches t
                WHERE EXISTS (
                    SELECT 1 FROM messages m
                    WHERE m.user_id = t.user_id AND m.chat_id > 0 AND m.role = 'user'
                      AND m.created_at >= t.created_at
                      AND LEFT(TRIM(COALESCE(m.content,'')), 1) <> '/'
                )
                GROUP BY t.ref_key
            ),
            ord_u AS (
                SELECT t.ref_key, COUNT(DISTINCT t.user_id) AS cnt
                FROM touches t
                JOIN orders o ON o.user_id = t.user_id AND o.created_at >= t.created_at
                GROUP BY t.ref_key
            ),
            paid_u AS (
                SELECT t.ref_key, COUNT(DISTINCT t.user_id) AS cnt,
                    COALESCE(SUM(p.amount_rub), 0) AS revenue
                FROM touches t
                JOIN orders o ON o.user_id = t.user_id AND o.status = 'paid'
                    AND o.paid_at >= t.created_at
                JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
                GROUP BY t.ref_key
            )
            SELECT COALESCE(rk.name, e.ref_key) AS name,
                   rk.type AS channel_type,
                   e.cnt AS entered,
                   COALESCE(a.cnt, 0) AS ai_dialog,
                   COALESCE(o.cnt, 0) AS ordered,
                   COALESCE(p.cnt, 0) AS paid,
                   COALESCE(p.revenue, 0) AS revenue
            FROM entered e
            LEFT JOIN ref_keys rk ON rk.ref_key = e.ref_key
            LEFT JOIN ai_u a ON a.ref_key = e.ref_key
            LEFT JOIN ord_u o ON o.ref_key = e.ref_key
            LEFT JOIN paid_u p ON p.ref_key = e.ref_key
            ORDER BY revenue DESC NULLS LAST, entered DESC
            LIMIT 25
            """,
            *ids,
        )

    async def _expiring_risk_summary(self, club_group_id: int) -> Dict[str, Any]:
        ex_l3, ex_l3_ids = self._ex("l.user_id", 2)
        row = await self._fetch_row(
            f"""
            WITH at_risk AS (
                SELECT l.user_id,
                       EXTRACT(DAY FROM (l.expires_at - NOW()))::int AS days_left
                FROM license l
                WHERE l.status = 'active'
                  AND l.expires_at > NOW()
                  AND l.expires_at < NOW() + INTERVAL '8 days'
                  {ex_l3}
                  AND NOT EXISTS (
                    SELECT 1 FROM messages m
                    WHERE m.user_id = l.user_id
                      AND m.chat_id = $1
                      AND m.role = 'user'
                      AND m.created_at > NOW() - INTERVAL '14 days'
                  )
            )
            SELECT
                COUNT(*)::int AS total_silent,
                COUNT(*) FILTER (WHERE days_left <= 3)::int AS days_1_3,
                COUNT(*) FILTER (WHERE days_left >= 4)::int AS days_4_7
            FROM at_risk
            """,
            club_group_id,
            *ex_l3_ids,
        )
        return dict(row) if row else {"total_silent": 0, "days_1_3": 0, "days_4_7": 0}

    async def _fetch_row(self, query: str, *args: Any) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, *args)
            return dict(row) if row else None

    async def _channel_overlap(self) -> Dict[str, Any]:
        ex, ids = self._ex("at.user_id", 1)
        summary = await self._fetch_row(
            f"""
            WITH touches AS (
                SELECT DISTINCT at.user_id,
                       COALESCE(NULLIF(TRIM(at.channel_type), ''),
                                NULLIF(TRIM(rk.type), ''), 'other') AS ch
                FROM attribution_touches at
                LEFT JOIN ref_keys rk ON rk.ref_key = at.ref_key
                WHERE at.ref_key IS NOT NULL
                {ex}
            ),
            per_user AS (
                SELECT user_id, COUNT(DISTINCT ch) AS nch
                FROM touches
                GROUP BY user_id
            )
            SELECT
                COUNT(*)::int AS total_users,
                COUNT(*) FILTER (WHERE nch = 1)::int AS single_channel,
                COUNT(*) FILTER (WHERE nch = 2)::int AS two_channels,
                COUNT(*) FILTER (WHERE nch = 3)::int AS three_channels,
                COUNT(*) FILTER (WHERE nch >= 4)::int AS four_plus_channels,
                COUNT(*) FILTER (WHERE nch >= 2)::int AS multi_channel
            FROM per_user
            """,
            *ids,
        )
        return dict(summary) if summary else {}

    async def _channel_funnel(self) -> List[Dict[str, Any]]:
        ex, ids = self._ex("at.user_id", 1)
        return await self._all(
            f"""
            WITH touches AS (
                SELECT at.user_id, COALESCE(at.channel_type, rk.type, 'other') AS ch
                FROM attribution_touches at
                LEFT JOIN ref_keys rk ON rk.ref_key = at.ref_key
                WHERE at.ref_key IS NOT NULL
                {ex}
            ),
            entered AS (
                SELECT ch, COUNT(DISTINCT user_id) AS cnt FROM touches GROUP BY ch
            ),
            paid_u AS (
                SELECT t.ch, COUNT(DISTINCT t.user_id) AS cnt,
                    COALESCE(SUM(p.amount_rub), 0) AS revenue
                FROM touches t
                JOIN orders o ON o.user_id = t.user_id AND o.status = 'paid'
                JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
                GROUP BY t.ch
            )
            SELECT e.ch AS channel_type, e.cnt AS entered,
                   COALESCE(p.cnt, 0) AS paid,
                   COALESCE(p.revenue, 0) AS revenue
            FROM entered e
            LEFT JOIN paid_u p ON p.ch = e.ch
            ORDER BY revenue DESC
            """,
            *ids,
        )

    async def _load_comparisons(self) -> Dict[str, Any]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT snapshot_date, total_amount, active_licenses, paid_orders
                FROM club_report_snapshots
                WHERE snapshot_date IN (
                    CURRENT_DATE - 1,
                    CURRENT_DATE - 2,
                    CURRENT_DATE - 8,
                    (CURRENT_DATE - INTERVAL '1 month')::date
                )
                ORDER BY snapshot_date DESC
                """
            )
        by_date = {r["snapshot_date"]: dict(r) for r in rows}
        return {"snapshots": {str(k): v for k, v in by_date.items()}}

    async def _run_llm_blocks(self, metrics: Dict[str, Any]) -> Dict[str, str]:
        api_key = config.DEEPSEEK_API_KEY or ""
        out: Dict[str, str] = {}
        if not self._storage or not api_key:
            return out

        group_text = await self._fetch_group_messages_yesterday()
        stats = (
            f"Активных в группе вчера: {metrics.get('club_group_active_yesterday', 0)}; "
            f"молчунов: {metrics.get('group_silent_count', 0)}"
        )
        g = await analyze_group_day(
            api_key=api_key, messages_blob=group_text, stats_line=stats
        )
        if g:
            out["group"] = g

        lead_dialogs = await self._fetch_lead_dialogs_for_llm()
        paid_dialogs = await self._fetch_paid_yesterday_dialogs()
        agg = {
            "funnel_72h": metrics.get("funnel_72h"),
            "ai_agent": metrics.get("ai_agent"),
        }
        l = await analyze_lead_dialogs(
            api_key=api_key,
            dialogs_blob=format_dialogs_for_llm(lead_dialogs),
            aggregates=agg,
            paid_brief_blob=format_dialogs_for_llm(paid_dialogs, max_messages=12),
        )
        if l:
            out["leads"] = l
        return out

    async def _fetch_group_messages_yesterday(self) -> str:
        if not self._club_group_id:
            return ""
        ex, ids = self._ex("m.user_id", 2)
        rows = await self._all(
            f"""
            SELECT m.user_id, u.username, u.first_name, m.content, m.created_at
            FROM messages m
            LEFT JOIN users u ON u.user_id = m.user_id
            WHERE m.chat_id = $1 AND m.role = 'user'
              AND (m.created_at AT TIME ZONE 'Europe/Moscow')::date
                  = (NOW() AT TIME ZONE 'Europe/Moscow')::date - 1
              AND COALESCE(m.content, '') <> ''
            {ex}
            ORDER BY m.created_at ASC
            LIMIT 400
            """,
            self._club_group_id,
            *ids,
        )
        lines = []
        for r in rows:
            who = r.get("username") or r.get("first_name") or r["user_id"]
            lines.append(f"@{who} ({r['created_at']}): {r['content']}")
        return "\n".join(lines)

    async def _fetch_lead_dialogs_for_llm(self) -> List[Dict[str, Any]]:
        if not self._storage:
            return []
        ex, ids = self._ex("m.user_id", 1)
        users = await self._all(
            f"""
            SELECT DISTINCT m.user_id,
                EXISTS (
                    SELECT 1 FROM license l
                    WHERE l.user_id = m.user_id
                      AND l.status = 'active'
                      AND l.expires_at > NOW()
                ) AS has_active_license
            FROM messages m
            WHERE m.chat_id > 0 AND m.role = 'user'
              AND (m.created_at AT TIME ZONE 'Europe/Moscow')::date
                  = (NOW() AT TIME ZONE 'Europe/Moscow')::date - 1
              AND LEFT(TRIM(COALESCE(m.content,'')), 1) <> '/'
            {ex}
            ORDER BY has_active_license ASC, m.user_id
            LIMIT 40
            """,
            *ids,
        )
        dialogs = []
        for u in users:
            uid = int(u["user_id"])
            hist = await self._storage.get_private_chat_history(uid, limit=20)
            if hist:
                dialogs.append({"user_id": uid, "messages": hist})
        return dialogs

    async def _fetch_paid_yesterday_dialogs(self) -> List[Dict[str, Any]]:
        if not self._storage:
            return []
        ex, ids = self._ex("o.user_id", 1)
        users = await self._all(
            f"""
            SELECT DISTINCT o.user_id
            FROM orders o
            JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
            WHERE o.status = 'paid'
              AND (o.paid_at AT TIME ZONE 'Europe/Moscow')::date
                  = (NOW() AT TIME ZONE 'Europe/Moscow')::date - 1
            {ex}
            LIMIT 15
            """,
            *ids,
        )
        dialogs = []
        for u in users:
            uid = int(u["user_id"])
            hist = await self._storage.get_private_chat_history(uid, limit=12)
            if hist:
                dialogs.append({"user_id": uid, "messages": hist})
        return dialogs
