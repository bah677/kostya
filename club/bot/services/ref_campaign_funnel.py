"""Воронка внешних кампаний по ref_key (attribution_touches).

Когорта: первое касание user + ref_key (first touch).
Якорь времени: ``created_at`` первого касания.

Этапы:
  entered   — первое касание ref_key;
  ai_dialog — осмысленное сообщение в личке после касания;
  ordered   — создан заказ после касания;
  paid      — оплачен заказ; revenue — сумма payments.amount_rub (succeeded).
"""

from __future__ import annotations

import html as html_mod
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, TYPE_CHECKING

from bot.services.report_exclude import sql_exclude_users

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

_REF_PREFIX_RE = re.compile(r"^ref_", re.IGNORECASE)
_FILTER_RE = re.compile(r"^(type|search)[:=](.+)$", re.IGNORECASE)

_CHANNEL_LABELS = {
    "tg": "Telegram",
    "telegram": "Telegram",
    "biblia_bot": "Библия бот",
    "other": "Другое",
}

_FUNNEL_SQL = """
WITH raw_touches AS (
    SELECT at.user_id, at.ref_key, at.created_at
    FROM attribution_touches at
    WHERE at.ref_key IS NOT NULL
      AND at.ref_key = ANY($1::text[])
      {exclude_at}
),
touches AS (
    SELECT DISTINCT ON (user_id, ref_key)
           user_id, ref_key, created_at
    FROM raw_touches
    ORDER BY user_id, ref_key, created_at ASC
),
entered AS (
    SELECT ref_key, COUNT(DISTINCT user_id) AS cnt FROM touches GROUP BY ref_key
),
ai_u AS (
    SELECT t.ref_key, COUNT(DISTINCT t.user_id) AS cnt
    FROM touches t
    WHERE EXISTS (
        SELECT 1 FROM messages m
        WHERE m.user_id = t.user_id
          AND m.chat_id > 0
          AND m.role = 'user'
          AND m.created_at >= t.created_at
          AND LEFT(TRIM(COALESCE(m.content, '')), 1) <> '/'
    )
    GROUP BY t.ref_key
),
ord_u AS (
    SELECT t.ref_key, COUNT(DISTINCT t.user_id) AS cnt
    FROM touches t
    JOIN orders o ON o.user_id = t.user_id AND o.created_at >= t.created_at
    {exclude_o}
    GROUP BY t.ref_key
),
paid_u AS (
    SELECT t.ref_key, COUNT(DISTINCT t.user_id) AS cnt,
        COALESCE(SUM(p.amount_rub), 0) AS revenue
    FROM touches t
    JOIN orders o ON o.user_id = t.user_id
        AND o.status = 'paid'
        AND o.paid_at >= t.created_at
    JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
    {exclude_o}
    GROUP BY t.ref_key
)
SELECT COALESCE(rk.name, e.ref_key) AS name,
       e.ref_key,
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
"""

_FUNNEL_TOTAL_SQL = """
WITH raw_touches AS (
    SELECT at.user_id, at.ref_key, at.created_at
    FROM attribution_touches at
    WHERE at.ref_key IS NOT NULL
      AND at.ref_key = ANY($1::text[])
      {exclude_at}
),
per_key AS (
    SELECT DISTINCT ON (user_id, ref_key)
           user_id, ref_key, created_at
    FROM raw_touches
    ORDER BY user_id, ref_key, created_at ASC
),
cohort AS (
    SELECT DISTINCT ON (user_id)
           user_id, created_at AS anchor_at
    FROM per_key
    ORDER BY user_id, created_at ASC
)
SELECT
    (SELECT COUNT(*)::int FROM cohort) AS entered,
    (
        SELECT COUNT(*)::int FROM cohort c
        WHERE EXISTS (
            SELECT 1 FROM messages m
            WHERE m.user_id = c.user_id
              AND m.chat_id > 0
              AND m.role = 'user'
              AND m.created_at >= c.anchor_at
              AND LEFT(TRIM(COALESCE(m.content, '')), 1) <> '/'
        )
    ) AS ai_dialog,
    (
        SELECT COUNT(*)::int FROM cohort c
        WHERE EXISTS (
            SELECT 1 FROM orders o
            WHERE o.user_id = c.user_id AND o.created_at >= c.anchor_at
            {exclude_o_exists}
        )
    ) AS ordered,
    (
        SELECT COUNT(*)::int FROM cohort c
        WHERE EXISTS (
            SELECT 1
            FROM orders o
            JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
            WHERE o.user_id = c.user_id
              AND o.status = 'paid'
              AND o.paid_at >= c.anchor_at
              {exclude_o_exists}
        )
    ) AS paid,
    (
        SELECT COALESCE(SUM(p.amount_rub), 0)::float
        FROM cohort c
        JOIN orders o ON o.user_id = c.user_id
            AND o.status = 'paid'
            AND o.paid_at >= c.anchor_at
        JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
        {exclude_o_join}
    ) AS revenue
"""


@dataclass(frozen=True)
class RefFunnelFilters:
    type_filter: Optional[str] = None
    search_filter: Optional[str] = None


@dataclass(frozen=True)
class RefFunnelArgs:
    explicit_keys: tuple[str, ...]
    filters: RefFunnelFilters


def normalize_ref_key(raw: str) -> str:
    """Нормализует ref_key: trim, снимает префикс ref_."""
    s = (raw or "").strip()
    if not s:
        return ""
    s = _REF_PREFIX_RE.sub("", s)
    return s.strip()


def parse_ref_funnel_args(args: Optional[str]) -> RefFunnelArgs:
    """Разбор аргументов: явные ключи + фильтры type:VALUE и search:VALUE."""
    explicit: list[str] = []
    type_filter: Optional[str] = None
    search_filter: Optional[str] = None

    raw = (args or "").replace(",", " ").split()
    for tok in raw:
        t = tok.strip()
        if not t:
            continue
        mo = _FILTER_RE.match(t)
        if mo:
            kind = mo.group(1).lower()
            val = (mo.group(2) or "").strip()
            if not val:
                continue
            if kind == "type":
                type_filter = val
            elif kind == "search":
                search_filter = val
            continue
        key = normalize_ref_key(t)
        if key:
            explicit.append(key)

    return RefFunnelArgs(
        explicit_keys=tuple(dict.fromkeys(explicit)),
        filters=RefFunnelFilters(
            type_filter=type_filter,
            search_filter=search_filter,
        ),
    )


async def resolve_ref_keys(
    pool: "asyncpg.Pool",
    explicit: Sequence[str],
    filters: RefFunnelFilters,
) -> List[str]:
    """Ключи из ref_keys и «сирот» attribution_touches по фильтрам."""
    exp = sorted({normalize_ref_key(k) for k in explicit if normalize_ref_key(k)})
    t = (filters.type_filter or "").strip() or None
    s = (filters.search_filter or "").strip() or None

    if exp and not t and not s:
        return exp

    matched = await _fetch_keys_matching_filters(pool, type_filter=t, search_filter=s)

    if exp:
        return sorted(set(exp) & set(matched))
    return matched


async def _fetch_keys_matching_filters(
    pool: "asyncpg.Pool",
    *,
    type_filter: Optional[str],
    search_filter: Optional[str],
) -> List[str]:
    conditions_rk: list[str] = []
    conditions_orphan: list[str] = []
    params: list[Any] = []
    idx = 1

    if type_filter:
        conditions_rk.append(f"rk.type = ${idx}")
        conditions_orphan.append(
            f"COALESCE(NULLIF(TRIM(at.channel_type), ''), 'other') = ${idx}"
        )
        params.append(type_filter)
        idx += 1

    if search_filter:
        pat = f"%{search_filter}%"
        conditions_rk.append(
            f"(rk.ref_key ILIKE ${idx} OR COALESCE(rk.name, '') ILIKE ${idx})"
        )
        conditions_orphan.append(f"at.ref_key ILIKE ${idx}")
        params.append(pat)
        idx += 1

    where_rk = f"WHERE {' AND '.join(conditions_rk)}" if conditions_rk else ""
    where_orphan = f"AND {' AND '.join(conditions_orphan)}" if conditions_orphan else ""

    query = f"""
    SELECT rk.ref_key FROM ref_keys rk {where_rk}
    UNION
    SELECT DISTINCT at.ref_key
    FROM attribution_touches at
    WHERE at.ref_key IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM ref_keys rk2 WHERE rk2.ref_key = at.ref_key
      )
      {where_orphan}
    ORDER BY 1
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return [str(r["ref_key"]) for r in rows if r.get("ref_key")]


async def collect_ref_funnel(
    pool: "asyncpg.Pool",
    ref_keys: Sequence[str],
) -> List[Dict[str, Any]]:
    keys = sorted({normalize_ref_key(k) for k in ref_keys if normalize_ref_key(k)})
    if not keys:
        return []

    ex_at, ex_ids = sql_exclude_users("at.user_id", start_param=2)
    ex_o = ex_at.replace("at.user_id", "o.user_id")

    query = _FUNNEL_SQL.format(exclude_at=ex_at, exclude_o=ex_o)

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, keys, *ex_ids)
        return [dict(r) for r in rows]


async def collect_ref_funnel_total(
    pool: "asyncpg.Pool",
    ref_keys: Sequence[str],
) -> Optional[Dict[str, Any]]:
    """Сводная воронка: каждый user один раз (якорь — первое касание среди выбранных ref)."""
    keys = sorted({normalize_ref_key(k) for k in ref_keys if normalize_ref_key(k)})
    if len(keys) < 2:
        return None

    ex_at, ex_ids = sql_exclude_users("at.user_id", start_param=2)
    ex_o_exists = ex_at.replace("at.user_id", "o.user_id")
    ex_o_join = ex_at.replace("at.user_id", "c.user_id")

    query = _FUNNEL_TOTAL_SQL.format(
        exclude_at=ex_at,
        exclude_o_exists=ex_o_exists,
        exclude_o_join=ex_o_join,
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, keys, *ex_ids)
        if not row:
            return None
        return dict(row)


async def collect_ref_funnel_report(
    pool: "asyncpg.Pool",
    ref_keys: Sequence[str],
) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], int]:
    """Строки по ключам + итог (если ключей ≥ 2)."""
    keys = sorted({normalize_ref_key(k) for k in ref_keys if normalize_ref_key(k)})
    rows = await collect_ref_funnel(pool, keys)
    total = await collect_ref_funnel_total(pool, keys)
    return rows, total, len(keys)


async def list_ref_catalog(
    pool: "asyncpg.Pool",
    *,
    days: int = 30,
) -> Dict[str, Any]:
    """Каталог ref_key: зарегистрированные, сироты, доступные типы."""
    days = max(1, int(days))
    ex_at, ex_at_ids = sql_exclude_users("at.user_id", start_param=2)

    registered_query = f"""
    SELECT
        rk.ref_key,
        rk.name,
        rk.type,
        FALSE AS is_orphan,
        COUNT(DISTINCT at.user_id) FILTER (
            WHERE at.created_at >= NOW() - make_interval(days => $1)
              {ex_at}
        )::int AS touches_recent,
        COUNT(DISTINCT at.user_id) FILTER (
            WHERE at.user_id IS NOT NULL {ex_at}
        )::int AS touches_lifetime
    FROM ref_keys rk
    LEFT JOIN attribution_touches at ON at.ref_key = rk.ref_key
    GROUP BY rk.ref_key, rk.name, rk.type
    ORDER BY touches_lifetime DESC NULLS LAST, rk.ref_key
    """

    orphan_query = f"""
    SELECT
        at.ref_key,
        NULL::text AS name,
        COALESCE(NULLIF(TRIM(at.channel_type), ''), 'other') AS type,
        TRUE AS is_orphan,
        COUNT(DISTINCT at.user_id) FILTER (
            WHERE at.created_at >= NOW() - make_interval(days => $1)
        )::int AS touches_recent,
        COUNT(DISTINCT at.user_id)::int AS touches_lifetime
    FROM attribution_touches at
    WHERE at.ref_key IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM ref_keys rk WHERE rk.ref_key = at.ref_key
      )
      {ex_at}
    GROUP BY at.ref_key, COALESCE(NULLIF(TRIM(at.channel_type), ''), 'other')
    ORDER BY touches_lifetime DESC NULLS LAST, at.ref_key
    """

    types_query = """
    SELECT DISTINCT type FROM ref_keys
    WHERE type IS NOT NULL AND TRIM(type) <> ''
    ORDER BY type
    """

    async with pool.acquire() as conn:
        registered = await conn.fetch(registered_query, days, *ex_at_ids)
        orphans = await conn.fetch(orphan_query, days, *ex_at_ids)
        types = await conn.fetch(types_query)

    return {
        "days": days,
        "registered": [dict(r) for r in registered],
        "orphans": [dict(r) for r in orphans],
        "types": [str(r["type"]) for r in types if r.get("type")],
    }


def _pct(part: int, whole: int) -> str:
    if whole <= 0:
        return "—"
    return f"{100.0 * part / whole:.1f}%"


def _money(rub: float) -> str:
    if rub >= 1_000_000:
        return f"{rub / 1_000_000:.2f} млн ₽"
    if rub >= 1_000:
        return f"{rub / 1_000:.1f} тыс ₽"
    return f"{rub:.0f} ₽"


def _channel_label(raw: Any) -> str:
    if raw is None or str(raw).strip() in ("", "None"):
        return "без канала"
    s = str(raw).strip()
    return _CHANNEL_LABELS.get(s.lower(), s)


def format_ref_playbook_html() -> str:
    return (
        "<b>🔗 Воронка ref-кампаний</b>\n\n"
        "<i>Когорта: первое касание user+ref_key. События — после него. "
        "Тестировщики исключены.</i>\n\n"
        "<b>Команды</b>\n"
        "• <code>/ref_funnel</code> — каталог ключей\n"
        "• <code>/ref_funnel KEY [KEY …]</code> — воронка по ключам\n"
        "• <code>/ref_funnel type:tg</code> — все ключи типа\n"
        "• <code>/ref_funnel search:promo</code> — поиск по имени/ключу\n"
        "• <code>/ref_key</code> — очередь ключей без псевдонима\n"
        "• <code>/campaign_funnel</code> — алиас\n\n"
        "<b>Цепочка</b>\n"
        "зашли → диалог с агентом → заказ → оплата\n\n"
        "<i>При нескольких ключах — блок <b>ИТОГО</b> (user без двойного счёта).</i>\n\n"
        "<i>«Сироты» — ref_key из attribution без записи в ref_keys.</i>"
    )


def format_ref_catalog_html(catalog: Dict[str, Any]) -> str:
    days = int(catalog.get("days") or 30)
    registered = catalog.get("registered") or []
    orphans = catalog.get("orphans") or []
    types = catalog.get("types") or []

    lines = [
        "<b>🔗 Каталог ref-кампаний</b>",
        f"<i>Касания за {days} дн. / lifetime · "
        "<code>/ref_funnel KEY</code> или фильтры <code>type:</code> "
        "<code>search:</code></i>",
        "",
    ]

    if types:
        type_s = ", ".join(
            f"<code>{html_mod.escape(t)}</code>" for t in types[:20]
        )
        lines.append(f"<b>Типы:</b> {type_s}")
        lines.append("")

    if registered:
        lines.append(f"<b>Зарегистрированные ({len(registered)})</b>")
        for row in registered[:40]:
            key = html_mod.escape(str(row.get("ref_key") or ""))
            name = html_mod.escape(str(row.get("name") or ""))
            ch = _channel_label(row.get("type"))
            recent = int(row.get("touches_recent") or 0)
            life = int(row.get("touches_lifetime") or 0)
            label = name if name else key
            lines.append(
                f"• <code>{key}</code> · {html_mod.escape(label)} · "
                f"{html_mod.escape(ch)} · {recent}/{life}"
            )
        if len(registered) > 40:
            lines.append(f"<i>… ещё {len(registered) - 40}</i>")
        lines.append("")

    if orphans:
        lines.append(f"<b>Сироты ({len(orphans)})</b>")
        lines.append(
            "<i>Задать псевдоним: <code>/ref_key</code> или кнопка в алерте.</i>"
        )
        for row in orphans[:25]:
            key = html_mod.escape(str(row.get("ref_key") or ""))
            ch = _channel_label(row.get("type"))
            recent = int(row.get("touches_recent") or 0)
            life = int(row.get("touches_lifetime") or 0)
            lines.append(
                f"• <code>{key}</code> · {html_mod.escape(ch)} · {recent}/{life}"
            )
        if len(orphans) > 25:
            lines.append(f"<i>… ещё {len(orphans) - 25}</i>")
        lines.append("")

    if not registered and not orphans:
        lines.append("Ключей с касаниями пока нет.")

    lines.append(
        "<i>Примеры: <code>/ref_funnel my_key</code> · "
        "<code>/ref_funnel type:tg</code> · "
        "<code>/ref_funnel search:promo</code></i>"
    )
    return "\n".join(lines)


def format_ref_funnel_html(
    rows: List[Dict[str, Any]],
    *,
    ref_keys: Optional[Sequence[str]] = None,
) -> str:
    if not rows:
        req = ""
        if ref_keys:
            req = f" ({', '.join(html_mod.escape(k) for k in ref_keys)})"
        return (
            "<b>🔗 Воронка ref-кампаний</b>\n\n"
            f"Нет данных по выбранным ключам{req}.\n"
            "Нужны касания в <code>attribution_touches</code> "
            "(backfill или живой трафик)."
        )

    lines = [
        "<b>🔗 Воронка ref-кампаний</b>",
        "<i>Когорта: первое касание user+ref_key. События — после него. "
        "Тестировщики исключены.</i>",
        "",
        "Цепочка: <b>зашли → диалог с агентом → заказ → оплата</b>",
        "",
    ]

    if total and keys_requested >= 2:
        n = keys_requested
        t_entered = int(total.get("entered") or 0)
        t_ai = int(total.get("ai_dialog") or 0)
        t_ord = int(total.get("ordered") or 0)
        t_paid = int(total.get("paid") or 0)
        t_rev = float(total.get("revenue") or 0)
        lines.extend(
            _format_funnel_block(
                title=f"<b>📊 ИТОГО</b> · {n} ключей",
                subtitle=(
                    "<i>Один пользователь считается один раз; якорь — "
                    "самое раннее касание среди выбранных ref.</i>"
                ),
                entered=t_entered,
                ai=t_ai,
                ordered=t_ord,
                paid=t_paid,
                rev=t_rev,
            )
        )
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")

    for row in rows:
        key = html_mod.escape(str(row.get("ref_key") or ""))
        name = html_mod.escape(str(row.get("name") or key))
        ch = html_mod.escape(_channel_label(row.get("channel_type")))

        entered = int(row.get("entered") or 0)
        ai = int(row.get("ai_dialog") or 0)
        ordered = int(row.get("ordered") or 0)
        paid = int(row.get("paid") or 0)
        rev = float(row.get("revenue") or 0)

        lines.append(f"<b>{name}</b> · <code>{key}</code> · {ch}")
        lines.append(
            f"• {entered} зашли → {ai} диалог → {ordered} заказ → "
            f"{paid} оплата · {_money(rev)}"
        )
        lines.append(
            f"   CR: ИИ {_pct(ai, entered)} · заказ {_pct(ordered, ai)} · "
            f"оплата {_pct(paid, ordered)} · сквозная {_pct(paid, entered)}"
        )
        lines.append("")

    return "\n".join(lines)
