"""Детальный отчёт по отвалу (просрочка, источники, поведение) — по запросу админа."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from bot.services.club_report_collect import ClubReportDailyCollector

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ABOUT_CLUB_PATHS = (
    _REPO_ROOT / "bot" / "texts" / "aboutclub.txt",
    _REPO_ROOT / "aboutclub.txt",
)


def load_aboutclub_text() -> str:
    for path in ABOUT_CLUB_PATHS:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            continue
    logger.warning("aboutclub.txt: файл не найден в %s", [str(p) for p in ABOUT_CLUB_PATHS])
    return ""


class ClubChurnReportCollector:
    """Сбор многомерной статистики по просрочке и активным подписчикам (для сравнения)."""

    def __init__(self, pool: "asyncpg.Pool", club_group_id: int) -> None:
        self._pool = pool
        self._club_group_id = int(club_group_id or 0)
        self._base = ClubReportDailyCollector(pool, club_group_id=club_group_id)

    async def _fetch_row(self, query: str, *args: Any) -> Optional[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, *args)
            return dict(row) if row else None

    async def _fetch_all(self, query: str, *args: Any) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
            return [dict(r) for r in rows]

    async def _fetch_scalar(self, query: str, *args: Any) -> Any:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def build_payload(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()

        expired_total = await self._base.get_users_with_expired_license()
        expired_td_only = await self._base.get_expired_license_promo_test1week_only_count()
        active_lic = await self._base.get_active_licenses_count()
        expiring_7d = await self._base.get_users_expiring_soon()
        funnel = await self._base.get_promo_week_to_base_funnel_totals()
        ex_by_src = await self._base.get_expired_license_by_acquisition_source()
        ex_by_typ = await self._base.get_expired_license_by_source_type()

        pay_profile_exp = await self._expired_payment_buckets()
        pay_profile_act = await self._active_payment_buckets()

        tenure_exp = await self._tenure_buckets("expired")
        tenure_act = await self._tenure_buckets("active")

        lic_types = await self._license_type_distribution()

        grp_ex = grp_act = None
        if self._club_group_id:
            grp_ex = await self._group_message_agg("expired")
            grp_act = await self._group_message_agg("active")

        prv_ex = await self._private_user_message_agg("expired")
        prv_act = await self._private_user_message_agg("active")

        ltv_ex = await self._ltv_stats("expired")
        ltv_act = await self._ltv_stats("active")

        ia_ex = await self._inactivity_stats("expired")
        ia_act = await self._inactivity_stats("active")

        q_exp = await self._questions_stats("expired")
        q_act = await self._questions_stats("active")

        fuex = await self._followup_expired_snap()
        base_orders_ex = await self._base_order_count_distribution("expired")
        promo_before_expire = await self._expired_promo_then_never_base()

        return {
            "generated_at_utc": now,
            "club_group_id_config": self._club_group_id,
            "summary": {
                "active_subscribers_like_report": active_lic,
                "expired_license_status_count": expired_total,
                "expired_only_promo_test1week_tariffs_all_payments": expired_td_only,
                "expiring_7_days_by_day_td": expiring_7d,
            },
            "funnel_first_promo_week_then_base_payment": funnel,
            "expired_users_by_first_ref_human_name_top": _cap_rows(ex_by_src, 40),
            "expired_users_by_ref_type": ex_by_typ,
            "expired_payment_buckets": pay_profile_exp,
            "active_subscribers_payment_buckets": pay_profile_act,
            "tenure_days_first_paid_to_license_end_expired": tenure_exp,
            "tenure_days_first_paid_to_now_active_subscribers": tenure_act,
            "license_row_type_among_expired": lic_types.get("expired"),
            "license_row_type_among_active": lic_types.get("active"),
            "messages_in_club_group_user_sender_stats_expired": grp_ex,
            "messages_in_club_group_user_sender_stats_active_subscribers": grp_act,
            "private_chat_inbound_messages_user_sender_expired": prv_ex,
            "private_chat_inbound_messages_active_subscribers": prv_act,
            "lifetime_payment_rub_succeeded_expired": ltv_ex,
            "lifetime_payment_rub_succeeded_active_subscribers": ltv_act,
            "days_since_last_interaction_log_expired": ia_ex,
            "days_since_last_interaction_log_active_subscribers": ia_act,
            "users_questions_asked_field_expired": q_exp,
            "users_questions_asked_field_active_subscribers": q_act,
            "followup_current_status_among_expired": fuex,
            "among_expired_had_promo_week_paid_but_never_base_after": promo_before_expire,
            "among_expired_paid_orders_count_buckets": base_orders_ex,
        }

    def format_admin_html(self, payload: Dict[str, Any]) -> str:
        """Человекочитаемый HTML для Telegram."""

        def esc(x: Any) -> str:
            from html import escape

            return escape(str(x), quote=False)

        lines: List[str] = [
            "<b>📉 ОТЧЁТ ПО ОТВАЛУ (просроченные лицензии)</b>",
            f"<i>Снято: <code>{esc(payload.get('generated_at_utc'))}</code>, "
            f"group_id в конфиге: <code>{esc(payload.get('club_group_id_config'))}</code></i>",
            "",
            "<b>1. Сводка</b>",
        ]
        s = payload.get("summary") or {}
        lines.append(f"• Активных подписчиков (как в дневном отчёте): {esc(s.get('active_subscribers_like_report'))}")
        lines.append(f"• Просрочено записей (<code>status=expired</code>), уников: {esc(s.get('expired_license_status_count'))}")
        lines.append(
            f"• Из них платили только <code>promo_test1week*</code> (любые оплаты): {esc(s.get('expired_only_promo_test1week_tariffs_all_payments'))}"
        )
        lines.append("")
        lines.append("<b>2. Истекают за 7 дней (активная лицензия)</b>")
        for row in (s.get("expiring_7_days_by_day_td") or []):
            lines.append(
                f"• {int(row['days_left'])} дн.: {int(row['user_count'])} чел. "
                f"(ТД: {int(row.get('td_count') or 0)})"
            )
        if not (s.get("expiring_7_days_by_day_td")):
            lines.append("• Нет")

        lines.append("")
        lines.append("<b>3. Тест-драйв → base (все время)</b>")
        fn = payload.get("funnel_first_promo_week_then_base_payment") or {}
        lines.append(f"• Купили промо неделю: {esc(fn.get('test_buyers_total'))} чел.")
        lines.append(f"• Позже оплатили <code>base</code>: {esc(fn.get('converted_to_base'))} ({esc(fn.get('conversion_pct'))} %)")

        lines.append("")
        lines.append("<b>4. Отвал по источнику прихода (первый /start ref_)</b>")
        for row in (payload.get("expired_users_by_first_ref_human_name_top") or []):
            lines.append(f"• {esc(row.get('source_name'))}: {int(row.get('user_count') or 0)} чел.")
        lines.append("")
        lines.append("<b>5. Отвал по типу источника (ref_keys.type)</b>")
        for row in payload.get("expired_users_by_ref_type") or []:
            lines.append(f"• <code>{esc(row.get('source_type'))}</code>: {int(row.get('user_count') or 0)} чел.")

        lines.append("")
        lines.append("<b>6. Платёжный профиль среди просроченных</b>")
        for k, v in (payload.get("expired_payment_buckets") or {}).items():
            lines.append(f"• {esc(k)}: {esc(v)}")

        lines.append("")
        lines.append("<b>7. Платёжный профиль среди активных подписчиков</b>")
        for k, v in (payload.get("active_subscribers_payment_buckets") or {}).items():
            lines.append(f"• {esc(k)}: {esc(v)}")

        lines.append("")
        lines.append("<b>8. Tenure дней от первой оплаты до окончания лицензии (просроч.)</b>")
        for row in payload.get("tenure_days_first_paid_to_license_end_expired") or []:
            lines.append(f"• <code>{esc(row.get('bucket'))}</code>: {esc(row.get('user_count'))} чел.")

        lines.append("")
        lines.append("<b>9. Tenure дней до «сегодня» от первой оплаты (активные)</b>")
        for row in payload.get("tenure_days_first_paid_to_now_active_subscribers") or []:
            lines.append(f"• <code>{esc(row.get('bucket'))}</code>: {esc(row.get('user_count'))} чел.")

        lines.append("")
        lines.append("<b>10. license_type строки licence (просроч. / активн.)</b>")
        lt = payload.get("license_row_type_among_expired") or []
        lines.append("<i>Просроченные:</i>")
        if not lt:
            lines.append("• —")
        for row in lt:
            lines.append(f"• <code>{esc(row.get('license_type'))}</code>: {int(row['user_count'])}")
        lt2 = payload.get("license_row_type_among_active") or []
        lines.append("<i>Активные:</i>")
        if not lt2:
            lines.append("• —")
        for row in lt2:
            lines.append(f"• <code>{esc(row.get('license_type'))}</code>: {int(row['user_count'])}")

        def _dump_stats(title: str, block: Optional[Dict[str, Any]]) -> None:
            lines.append("")
            lines.append(title)
            if not block:
                lines.append("• Нет данных (или group_id не задан).")
                return
            for k in ("n", "avg", "median", "p90", "min_v", "max_v", "share_zero"):
                if k in block and block[k] is not None:
                    lines.append(f"• {esc(k)}: <code>{esc(block[k])}</code>")

        _dump_stats(
            "<b>11. Сообщения пользователя в группе клуба (просроч.)</b>",
            payload.get("messages_in_club_group_user_sender_stats_expired"),
        )
        _dump_stats(
            "<b>12. То же среди активных подписчиков</b>",
            payload.get("messages_in_club_group_user_sender_stats_active_subscribers"),
        )
        _dump_stats(
            "<b>13. Входящие в личку боту (просроч.)</b>",
            payload.get("private_chat_inbound_messages_user_sender_expired"),
        )
        _dump_stats(
            "<b>14. Входящие в личку (активные)</b>",
            payload.get("private_chat_inbound_messages_active_subscribers"),
        )
        _dump_stats(
            "<b>15. Сумма успешных платежей ₽ lifetime (просроч.)</b>",
            payload.get("lifetime_payment_rub_succeeded_expired"),
        )
        _dump_stats(
            "<b>16. То же активные</b>",
            payload.get("lifetime_payment_rub_succeeded_active_subscribers"),
        )
        _dump_stats(
            "<b>17. Дней с последнего interaction_logs (просроч.)</b>",
            payload.get("days_since_last_interaction_log_expired"),
        )
        _dump_stats(
            "<b>18. Дней с последнего interaction_logs (активные)</b>",
            payload.get("days_since_last_interaction_log_active_subscribers"),
        )
        _dump_stats(
            "<b>19. Поле users.questions_asked (просроч.)</b>",
            payload.get("users_questions_asked_field_expired"),
        )
        _dump_stats(
            "<b>20. Поле users.questions_asked (активные)</b>",
            payload.get("users_questions_asked_field_active_subscribers"),
        )

        lines.append("")
        lines.append("<b>21. Followup состояние (только среди просроч.)</b>")
        for row in payload.get("followup_current_status_among_expired") or []:
            lines.append(f"• status <code>{esc(row.get('status'))}</code>: {int(row['user_count'])}")

        lines.append("")
        lines.append("<b>22. Просроч., купили промо неделю, base после — никогда</b>")
        v = payload.get("among_expired_had_promo_week_paid_but_never_base_after")
        lines.append(f"• {esc(v)} чел.")

        lines.append("")
        lines.append("<b>23. Число успешных оплаченных заказов на пользователя (просроч.)</b>")
        for row in payload.get("among_expired_paid_orders_count_buckets") or []:
            lines.append(f"• <code>{esc(row.get('bucket'))}</code>: {int(row['user_count'])}")

        return "\n".join(lines)

    def payload_json_for_llm(self, payload: Dict[str, Any], max_chars: int = 120_000) -> str:
        raw = json.dumps(payload, ensure_ascii=False, default=str, indent=2)
        if len(raw) <= max_chars:
            return raw
        return raw[: max_chars - 80] + "\n\n… <truncated> …"

    # --- SQL helpers ---

    async def _expired_payment_buckets(self) -> Dict[str, int]:
        q = """
        WITH ex AS (
            SELECT DISTINCT user_id FROM license WHERE status = 'expired'
        ),
        flags AS (
            SELECT
                eu.user_id,
                EXISTS (
                    SELECT 1 FROM orders o
                    JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
                    JOIN tariffs t ON t.id = o.tariff_id
                    WHERE o.user_id = eu.user_id AND o.status = 'paid'
                      AND COALESCE(t.type, '') = 'base'
                ) AS had_base,
                EXISTS (
                    SELECT 1 FROM orders o
                    JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
                    WHERE o.user_id = eu.user_id AND o.status = 'paid'
                ) AS had_any_paid,
                EXISTS (
                    SELECT 1 FROM orders o
                    JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
                    JOIN tariffs t ON t.id = o.tariff_id
                    WHERE o.user_id = eu.user_id AND o.status = 'paid'
                      AND COALESCE(t.type, '') LIKE 'promo_test1week%'
                ) AS had_promo_week,
                NOT EXISTS (
                    SELECT 1 FROM orders o
                    JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
                    JOIN tariffs t ON t.id = o.tariff_id
                    WHERE o.user_id = eu.user_id AND o.status = 'paid'
                      AND COALESCE(t.type, '') NOT LIKE 'promo_test1week%'
                ) AS no_non_promo_tariff
            FROM ex eu
        )
        SELECT
            COUNT(*) FILTER (WHERE had_base) AS ever_paid_base,
            COUNT(*) FILTER (WHERE had_any_paid AND NOT had_base AND had_promo_week AND no_non_promo_tariff) AS paid_only_promo_week_family,
            COUNT(*) FILTER (WHERE had_any_paid AND NOT had_base AND NOT (had_promo_week AND no_non_promo_tariff)) AS paid_but_no_base_other_tariffs,
            COUNT(*) FILTER (WHERE NOT had_any_paid) AS no_successful_payments
        FROM flags
        """
        row = await self._fetch_row(q)
        return {k: int(row[k] or 0) for k in dict(row).keys()} if row else {}

    async def _active_payment_buckets(self) -> Dict[str, int]:
        """Активные подписчики как в метрике active_licenses: не bonus, истекает позже NOW."""
        q = """
        WITH act AS (
            SELECT DISTINCT user_id FROM license
            WHERE status = 'active' AND license_type <> 'bonus' AND expires_at > NOW()
        ),
        flags AS (
            SELECT
                eu.user_id,
                EXISTS (
                    SELECT 1 FROM orders o
                    JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
                    JOIN tariffs t ON t.id = o.tariff_id
                    WHERE o.user_id = eu.user_id AND o.status = 'paid'
                      AND COALESCE(t.type, '') = 'base'
                ) AS had_base,
                EXISTS (
                    SELECT 1 FROM orders o
                    JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
                    WHERE o.user_id = eu.user_id AND o.status = 'paid'
                ) AS had_any_paid,
                EXISTS (
                    SELECT 1 FROM orders o
                    JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
                    JOIN tariffs t ON t.id = o.tariff_id
                    WHERE o.user_id = eu.user_id AND o.status = 'paid'
                      AND COALESCE(t.type, '') LIKE 'promo_test1week%'
                ) AS had_promo_week,
                NOT EXISTS (
                    SELECT 1 FROM orders o
                    JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
                    JOIN tariffs t ON t.id = o.tariff_id
                    WHERE o.user_id = eu.user_id AND o.status = 'paid'
                      AND COALESCE(t.type, '') NOT LIKE 'promo_test1week%'
                ) AS no_non_promo_tariff
            FROM act eu
        )
        SELECT
            COUNT(*) FILTER (WHERE had_base) AS ever_paid_base,
            COUNT(*) FILTER (WHERE had_any_paid AND NOT had_base AND had_promo_week AND no_non_promo_tariff) AS paid_only_promo_week_family,
            COUNT(*) FILTER (WHERE had_any_paid AND NOT had_base AND NOT (had_promo_week AND no_non_promo_tariff)) AS paid_but_no_base_other_tariffs,
            COUNT(*) FILTER (WHERE NOT had_any_paid) AS no_successful_payments
        FROM flags
        """
        row = await self._fetch_row(q)
        return {k: int(row[k] or 0) for k in dict(row).keys()} if row else {}

    async def _tenure_buckets(self, which: str) -> List[Dict[str, Any]]:
        if which == "expired":
            lic_filter = "l.status = 'expired'"
            days_expr = "(l.expires_at - fp.first_paid_at)"
        else:
            lic_filter = "l.status = 'active' AND l.license_type <> 'bonus' AND l.expires_at > NOW()"
            days_expr = "(NOW() - fp.first_paid_at)"

        q = f"""
        WITH first_pay AS (
            SELECT DISTINCT ON (o.user_id)
                o.user_id,
                o.paid_at AS first_paid_at
            FROM orders o
            JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
            WHERE o.status = 'paid'
            ORDER BY o.user_id, o.paid_at ASC
        ),
        span AS (
            SELECT
                l.user_id,
                GREATEST(
                    0,
                    EXTRACT(EPOCH FROM {days_expr}) / 86400.0
                )::numeric AS days
            FROM license l
            JOIN first_pay fp ON fp.user_id = l.user_id
            WHERE {lic_filter}
        )
        SELECT
            CASE
                WHEN days < 7 THEN '0-6'
                WHEN days < 15 THEN '7-14'
                WHEN days < 31 THEN '15-30'
                WHEN days < 61 THEN '31-60'
                WHEN days < 91 THEN '61-90'
                WHEN days < 181 THEN '91-180'
                WHEN days < 366 THEN '181-365'
                ELSE '366+'
            END AS bucket,
            COUNT(*)::bigint AS user_count
        FROM span
        GROUP BY 1
        """
        rows = await self._fetch_all(q)
        order = ["0-6", "7-14", "15-30", "31-60", "61-90", "91-180", "181-365", "366+"]
        seen = {r["bucket"]: int(r["user_count"]) for r in rows}
        return [{"bucket": b, "user_count": seen.get(b, 0)} for b in order]

    async def _license_type_distribution(self) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = {}
        for key, cond in (
            ("expired", "status = 'expired'"),
            ("active", "status = 'active' AND expires_at > NOW()"),
        ):
            q = f"""
            SELECT COALESCE(license_type::text, 'null') AS license_type, COUNT(*)::bigint AS user_count
            FROM license WHERE {cond}
            GROUP BY 1 ORDER BY user_count DESC
            """
            out[key] = await self._fetch_all(q)
        return out

    async def _group_message_agg(self, which: str) -> Optional[Dict[str, Any]]:
        if not self._club_group_id:
            return None
        if which == "expired":
            cohort = "SELECT DISTINCT user_id FROM license WHERE status = 'expired'"
        else:
            cohort = """
            SELECT DISTINCT user_id FROM license
            WHERE status = 'active' AND license_type <> 'bonus' AND expires_at > NOW()
            """

        q = f"""
        WITH cohort AS ({cohort}),
        cnt AS (
            SELECT c.user_id, COUNT(m.id)::bigint AS c
            FROM cohort c
            LEFT JOIN messages m ON m.user_id = c.user_id
                AND m.chat_id = $1
                AND m.sender_type = 'user'
                AND m.deleted_at IS NULL
            GROUP BY c.user_id
        )
        SELECT
            COUNT(*)::bigint AS n,
            AVG(c::float8) AS avg,
            MIN(c::float8) AS min_v,
            MAX(c::float8) AS max_v,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY c::float8) AS median,
            percentile_cont(0.9) WITHIN GROUP (ORDER BY c::float8) AS p90,
            AVG((c = 0)::int::float8) AS share_zero
        FROM cnt
        """
        row = await self._fetch_row(q, self._club_group_id)
        return _row_to_stats(row)

    async def _private_user_message_agg(self, which: str) -> Optional[Dict[str, Any]]:
        if which == "expired":
            cohort = "SELECT DISTINCT user_id FROM license WHERE status = 'expired'"
        else:
            cohort = """
            SELECT DISTINCT user_id FROM license
            WHERE status = 'active' AND license_type <> 'bonus' AND expires_at > NOW()
            """

        q = f"""
        WITH cohort AS ({cohort}),
        cnt AS (
            SELECT c.user_id, COUNT(m.id)::bigint AS c
            FROM cohort c
            LEFT JOIN messages m ON m.user_id = c.user_id
                AND m.sender_type = 'user'
                AND m.chat_type = 'private'
                AND m.deleted_at IS NULL
            GROUP BY c.user_id
        )
        SELECT
            COUNT(*)::bigint AS n,
            AVG(c::float8) AS avg,
            MIN(c::float8) AS min_v,
            MAX(c::float8) AS max_v,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY c::float8) AS median,
            percentile_cont(0.9) WITHIN GROUP (ORDER BY c::float8) AS p90,
            AVG((c = 0)::int::float8) AS share_zero
        FROM cnt
        """
        row = await self._fetch_row(q)
        return _row_to_stats(row)

    async def _ltv_stats(self, which: str) -> Optional[Dict[str, Any]]:
        if which == "expired":
            cohort = "SELECT DISTINCT user_id FROM license WHERE status = 'expired'"
        else:
            cohort = """
            SELECT DISTINCT user_id FROM license
            WHERE status = 'active' AND license_type <> 'bonus' AND expires_at > NOW()
            """
        q = f"""
        WITH cohort AS ({cohort}),
        s AS (
            SELECT c.user_id, COALESCE(SUM(p.amount_rub), 0)::float8 AS rub
            FROM cohort c
            LEFT JOIN payments p ON p.user_id = c.user_id AND p.status = 'succeeded'
            GROUP BY c.user_id
        )
        SELECT
            COUNT(*)::bigint AS n,
            AVG(rub) AS avg,
            MIN(rub) AS min_v,
            MAX(rub) AS max_v,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY rub) AS median,
            percentile_cont(0.9) WITHIN GROUP (ORDER BY rub) AS p90,
            AVG((rub = 0)::int::float8) AS share_zero
        FROM s
        """
        row = await self._fetch_row(q)
        return _row_to_stats(row)

    async def _inactivity_stats(self, which: str) -> Optional[Dict[str, Any]]:
        if which == "expired":
            cohort = "SELECT DISTINCT user_id FROM license WHERE status = 'expired'"
        else:
            cohort = """
            SELECT DISTINCT user_id FROM license
            WHERE status = 'active' AND license_type <> 'bonus' AND expires_at > NOW()
            """
        q = f"""
        WITH cohort AS ({cohort}),
        last_ev AS (
            SELECT
                c.user_id,
                COALESCE(MAX(il.created_at), TIMESTAMP '1970-01-01') AS last_at
            FROM cohort c
            LEFT JOIN interaction_logs il ON il.user_id = c.user_id
            GROUP BY c.user_id
        ),
        d AS (
            SELECT EXTRACT(EPOCH FROM (NOW() - last_at)) / 86400.0 AS days_since
            FROM last_ev
        )
        SELECT
            COUNT(*)::bigint AS n,
            AVG(days_since::float8) AS avg,
            MIN(days_since::float8) AS min_v,
            MAX(days_since::float8) AS max_v,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY days_since::float8) AS median,
            percentile_cont(0.9) WITHIN GROUP (ORDER BY days_since::float8) AS p90,
            NULL::float8 AS share_zero
        FROM d
        """
        row = await self._fetch_row(q)
        return _row_to_stats(row)

    async def _questions_stats(self, which: str) -> Optional[Dict[str, Any]]:
        if which == "expired":
            cohort = "SELECT DISTINCT user_id FROM license WHERE status = 'expired'"
        else:
            cohort = """
            SELECT DISTINCT user_id FROM license
            WHERE status = 'active' AND license_type <> 'bonus' AND expires_at > NOW()
            """
        q = f"""
        WITH cohort AS ({cohort})
        SELECT
            COUNT(*)::bigint AS n,
            AVG(u.questions_asked::float8) AS avg,
            MIN(u.questions_asked::float8) AS min_v,
            MAX(u.questions_asked::float8) AS max_v,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY u.questions_asked::float8) AS median,
            percentile_cont(0.9) WITHIN GROUP (ORDER BY u.questions_asked::float8) AS p90,
            AVG((COALESCE(u.questions_asked,0) = 0)::int::float8) AS share_zero
        FROM cohort c
        JOIN users u ON u.user_id = c.user_id
        """
        row = await self._fetch_row(q)
        return _row_to_stats(row)

    async def _followup_expired_snap(self) -> List[Dict[str, Any]]:
        q = """
        SELECT fs.status::text AS status, COUNT(*)::bigint AS user_count
        FROM followup_states fs
        JOIN (SELECT DISTINCT user_id FROM license WHERE status = 'expired') x ON x.user_id = fs.user_id
        GROUP BY fs.status
        ORDER BY user_count DESC
        """
        return await self._fetch_all(q)

    async def _expired_promo_then_never_base(self) -> int:
        """Просроч., есть оплата promo_test1week*, но никогда успешной base после первого такого платежа."""
        q = """
        WITH ex AS (
            SELECT DISTINCT user_id FROM license WHERE status = 'expired'
        ),
        first_promo AS (
            SELECT DISTINCT ON (o.user_id)
                o.user_id,
                o.paid_at AS t0
            FROM orders o
            JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
            JOIN tariffs t ON t.id = o.tariff_id
            JOIN ex e ON e.user_id = o.user_id
            WHERE o.status = 'paid'
              AND COALESCE(t.type, '') LIKE 'promo_test1week%'
            ORDER BY o.user_id, o.paid_at ASC
        )
        SELECT COUNT(*)::bigint AS cnt
        FROM first_promo fp
        WHERE NOT EXISTS (
            SELECT 1 FROM orders o2
            JOIN payments p2 ON p2.order_id = o2.id AND p2.status = 'succeeded'
            JOIN tariffs t2 ON t2.id = o2.tariff_id
            WHERE o2.user_id = fp.user_id
              AND o2.status = 'paid'
              AND COALESCE(t2.type, '') = 'base'
              AND o2.paid_at > fp.t0
        )
        """
        v = await self._fetch_scalar(q)
        return int(v or 0)

    async def _base_order_count_distribution(self, which: str) -> List[Dict[str, Any]]:
        if which != "expired":
            return []
        q = """
        WITH ex AS (
            SELECT DISTINCT user_id FROM license WHERE status = 'expired'
        ),
        oc AS (
            SELECT
                e.user_id,
                (
                    SELECT COUNT(*)::bigint
                    FROM orders o
                    INNER JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
                    WHERE o.user_id = e.user_id AND o.status = 'paid'
                ) AS n
            FROM ex e
        ),
        tagged AS (
            SELECT user_id, n,
                CASE
                    WHEN COALESCE(n, 0) = 0 THEN '0'
                    WHEN n = 1 THEN '1'
                    WHEN n <= 3 THEN '2-3'
                    WHEN n <= 6 THEN '4-6'
                    ELSE '7+'
                END AS bucket
            FROM oc
        )
        SELECT bucket, COUNT(*)::bigint AS user_count
        FROM tagged
        GROUP BY bucket
        """
        rows = await self._fetch_all(q)
        order = ["0", "1", "2-3", "4-6", "7+"]
        mp = {r["bucket"]: int(r["user_count"]) for r in rows}
        return [{"bucket": b, "user_count": mp.get(b, 0)} for b in order]


def _cap_rows(rows: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    return rows[:limit] if rows else []


def _row_to_stats(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    out: Dict[str, Any] = {}
    n = int(row.get("n") or 0)
    out["n"] = n
    for k in ("avg", "median", "p90", "min_v", "max_v", "share_zero"):
        v = row.get(k)
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        out[k] = round(fv, 4) if k != "share_zero" else round(fv, 4)
    return out or None
