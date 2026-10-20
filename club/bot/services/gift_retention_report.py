"""Отчёт по тестовым когортам удержания подарочной волны.

Вывод для админа идёт в бот (`/gift_retention`), отдельного CLI нет.
Что проверяем и как раскладывались когорты — docs/gift_retention_experiment.md.
"""

from __future__ import annotations

import logging
from html import escape as html_escape
from typing import Any, Dict, List

from storage.db.gift_retention_experiment import (
    CAMPAIGN_GIFT_2026_09,
    VARIANT_CONTROL,
)

logger = logging.getLogger(__name__)

#: Что проверяет каждый вариант — подпись в отчёте.
VARIANT_TITLES: Dict[str, str] = {
    "p1_discount": "скидка 30% — 840 ₽",
    "p2_sell5d": "письмо −5д продаёт",
    "p3_group_close": "честно про группу",
    "p4_step299": "две недели 299 ₽",
    VARIANT_CONTROL: "контроль (как было)",
}

# Оплатой считаем только succeeded от 100 ₽ и только после попадания в
# когорту: привязки карты на 15 ₽ и отменённые попытки в зачёт не идут.
_SQL = """
WITH e AS (
    SELECT user_id, variant, assigned_at
    FROM gift_retention_experiment
    WHERE campaign = $1
),
sent AS (
    SELECT e.variant, s.outreach_slug, count(DISTINCT s.user_id) AS n
    FROM e JOIN subscription_outreach_sent s ON s.user_id = e.user_id
    WHERE s.outreach_slug LIKE 'expiry_minus_%'
    GROUP BY 1, 2
),
paid AS (
    SELECT e.variant,
           count(DISTINCT p.user_id) AS payers,
           coalesce(sum(p.amount), 0) AS revenue
    FROM e JOIN payments p ON p.user_id = e.user_id
    WHERE p.status = 'succeeded'
      AND p.created_at >= e.assigned_at
      AND p.amount >= 100
    GROUP BY 1
),
replied AS (
    SELECT e.variant, count(DISTINCT m.user_id) AS n
    FROM e JOIN messages m ON m.user_id = e.user_id
    WHERE m.sender_type = 'user' AND m.created_at >= e.assigned_at
    GROUP BY 1
)
SELECT e.variant,
       count(*) AS cohort,
       coalesce(max(pd.payers), 0) AS payers,
       coalesce(max(pd.revenue), 0) AS revenue,
       coalesce(max(rp.n), 0) AS replied,
       coalesce(max(s10.n), 0) AS sent_10d,
       coalesce(max(s5.n), 0) AS sent_5d,
       coalesce(max(s1.n), 0) AS sent_1d
FROM e
LEFT JOIN paid pd ON pd.variant = e.variant
LEFT JOIN replied rp ON rp.variant = e.variant
LEFT JOIN sent s10 ON s10.variant = e.variant AND s10.outreach_slug = 'expiry_minus_10d_ord1'
LEFT JOIN sent s5  ON s5.variant  = e.variant AND s5.outreach_slug  = 'expiry_minus_5d_ord1'
LEFT JOIN sent s1  ON s1.variant  = e.variant AND s1.outreach_slug  = 'expiry_minus_1d_ord1'
GROUP BY e.variant
ORDER BY (e.variant = 'control'), e.variant
"""

_STRATA_SQL = """
SELECT stratum,
       count(*) FILTER (WHERE variant = 'p1_discount')    AS p1,
       count(*) FILTER (WHERE variant = 'p2_sell5d')      AS p2,
       count(*) FILTER (WHERE variant = 'p3_group_close') AS p3,
       count(*) FILTER (WHERE variant = 'p4_step299')     AS p4,
       count(*) FILTER (WHERE variant = 'control')        AS ctl
FROM gift_retention_experiment
WHERE campaign = $1
GROUP BY stratum
ORDER BY stratum
"""


async def collect_gift_retention_report(
    user_storage, *, campaign: str = CAMPAIGN_GIFT_2026_09
) -> Dict[str, Any]:
    try:
        async with user_storage.get_connection() as conn:
            rows = [dict(r) for r in await conn.fetch(_SQL, campaign)]
            strata = [dict(r) for r in await conn.fetch(_STRATA_SQL, campaign)]
    except Exception as e:
        logger.error("collect_gift_retention_report %s: %s", campaign, e)
        return {"campaign": campaign, "rows": [], "strata": [], "error": str(e)}
    return {"campaign": campaign, "rows": rows, "strata": strata, "error": ""}


def format_gift_retention_report_html(data: Dict[str, Any]) -> str:
    campaign = html_escape(str(data.get("campaign") or ""))
    if data.get("error"):
        return (
            f"❌ <b>Когорты удержания · {campaign}</b>\n\n"
            f"Не удалось собрать отчёт: {html_escape(str(data['error'])[:300])}"
        )
    rows: List[Dict[str, Any]] = data.get("rows") or []
    if not rows:
        return (
            f"🧪 <b>Когорты удержания · {campaign}</b>\n\n"
            "Когорты ещё не размечены. Раскладка применяется при накате прода "
            "(<code>scripts/deploy_prod.sh</code>), шаг «когорты удержания»."
        )

    lines = [
        f"{'вариант':<15}{'чел':>4}{'−10д':>6}{'−5д':>5}{'−1д':>5}"
        f"{'отв':>5}{'опл':>5}{'конв':>7}{'сумма':>8}"
    ]
    lines.append("-" * len(lines[0]))
    for r in rows:
        cohort = int(r["cohort"] or 0)
        payers = int(r["payers"] or 0)
        conv = (100.0 * payers / cohort) if cohort else 0.0
        lines.append(
            f"{str(r['variant'])[:15]:<15}{cohort:>4}"
            f"{int(r['sent_10d']):>6}{int(r['sent_5d']):>5}{int(r['sent_1d']):>5}"
            f"{int(r['replied']):>5}{payers:>5}{conv:>6.0f}%{int(r['revenue']):>8}"
        )

    legend = "\n".join(
        f"· <code>{html_escape(r['variant'])}</code> — {html_escape(VARIANT_TITLES.get(r['variant'], ''))}"
        for r in rows
    )

    out = [
        f"🧪 <b>Когорты удержания · {campaign}</b>",
        "",
        f"<pre>{html_escape(chr(10).join(lines))}</pre>",
        legend,
        "",
        "<i>«отв» — написали боту после попадания в когорту. Оплата — только "
        "успешная, от 100 ₽ (привязки карты на 15 ₽ не в счёт).</i>",
    ]

    strata = data.get("strata") or []
    if strata:
        st_lines = [f"{'страта':<22}{'p1':>4}{'p2':>4}{'p3':>4}{'p4':>4}{'ctl':>5}"]
        for s in strata:
            st_lines.append(
                f"{str(s['stratum'])[:22]:<22}{int(s['p1']):>4}{int(s['p2']):>4}"
                f"{int(s['p3']):>4}{int(s['p4']):>4}{int(s['ctl']):>5}"
            )
        out += [
            "",
            "<b>Баланс когорт по стратам</b>",
            f"<pre>{html_escape(chr(10).join(st_lines))}</pre>",
        ]

    out += [
        "",
        "<i>Когорты по 10 человек: одна оплата — это 10 процентных пунктов. "
        "Цифры показывают направление, а не значимую разницу.</i>",
    ]
    return "\n".join(out)
