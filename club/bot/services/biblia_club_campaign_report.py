"""Сквозной отчёт: рассылки Библия-бот → переходы и воронка в клубном боте.

Берём кампании из БД Библии (mailing_campaigns) за последние 30 дней и ближайшие 3 дня,
где в тексте или кнопках есть ссылка на клубный бот. По ключам deep link считаем
переходы в club_db (attribution_touches) и воронку в двух сегментах:
  • впервые — не было касаний /start до этого перехода;
  • повторно — уже запускали бота раньше.

Пользователи с действующей лицензией на момент перехода в воронку не входят
(учитываются отдельно как «уже клиенты»).
"""

from __future__ import annotations

import html as html_mod
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, TYPE_CHECKING
from zoneinfo import ZoneInfo

from bot.services.report_exclude import sql_exclude_users

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

_START_URL_RE = re.compile(
    r"(?:https?://)?t\.me/(?:[A-Za-z0-9_]+)\?start=([A-Za-z0-9_%-]+)",
    re.IGNORECASE,
)

_TEST_CAMPAIGN_NAME_RE = re.compile(r"тест|test", re.IGNORECASE)


def is_test_campaign_name(name: Optional[str]) -> bool:
    """Кампании с «тест»/«test» в названии не попадают в отчёт."""
    return bool(_TEST_CAMPAIGN_NAME_RE.search(name or ""))

_BIBLIA_CAMPAIGNS_SQL = """
SELECT
    mc.id,
    mc.name,
    mc.status,
    mc.scheduled_at,
    mc.campaign_source,
    mc.sent_count,
    mc.failed_count,
    mc.blocked_count,
    COUNT(ma.id)::bigint AS audience_size,
    COUNT(*) FILTER (WHERE ma.status = 'sent')::bigint AS audience_sent,
    mc.text,
    mc.buttons
FROM mailing_campaigns mc
LEFT JOIN mailing_audience ma ON ma.campaign_id = mc.id
WHERE mc.scheduled_at >= $1::timestamptz
  AND mc.scheduled_at <= $2::timestamptz
  AND mc.status IN ('planned', 'running', 'completed')
  AND (
      mc.text ILIKE '%' || $3 || '%'
      OR mc.buttons::text ILIKE '%' || $3 || '%'
  )
  AND mc.name NOT ILIKE '%тест%'
  AND mc.name NOT ILIKE '%test%'
GROUP BY mc.id
ORDER BY mc.scheduled_at DESC, mc.id DESC
"""

_CLUB_FUNNEL_SQL = """
WITH params AS (
    SELECT $1::timestamptz AS anchor_at, $2::text[] AS touch_keys
),
cohort_touches AS (
    SELECT DISTINCT ON (at.user_id)
        at.user_id,
        at.touch_key,
        at.created_at AS touch_at
    FROM attribution_touches at, params p
    WHERE at.source_type = 'start'
      AND at.touch_key = ANY(p.touch_keys)
      AND at.created_at >= p.anchor_at
      {exclude_at}
    ORDER BY at.user_id, at.created_at ASC
),
classified AS (
    SELECT
        ct.user_id,
        ct.touch_key,
        ct.touch_at,
        EXISTS (
            SELECT 1 FROM license l
            WHERE l.user_id = ct.user_id
              AND l.status = 'active'
              AND l.expires_at > ct.touch_at
        ) AS is_active_client,
        NOT EXISTS (
            SELECT 1 FROM attribution_touches at2
            WHERE at2.user_id = ct.user_id
              AND at2.source_type = 'start'
              AND at2.created_at < ct.touch_at
        ) AS is_first_launch
    FROM cohort_touches ct
),
tagged AS (
    SELECT
        user_id,
        touch_at,
        CASE
            WHEN is_active_client THEN 'client'
            WHEN is_first_launch THEN 'first'
            ELSE 'repeat'
        END AS segment
    FROM classified
),
funnel_eligible AS (
    SELECT * FROM tagged WHERE segment IN ('first', 'repeat')
),
ai_u AS (
    SELECT DISTINCT f.segment, f.user_id
    FROM funnel_eligible f
    WHERE EXISTS (
        SELECT 1 FROM messages m
        WHERE m.user_id = f.user_id
          AND m.chat_id > 0
          AND m.role = 'user'
          AND m.created_at >= f.touch_at
          AND LEFT(TRIM(COALESCE(m.content, '')), 1) <> '/'
    )
),
ord_u AS (
    SELECT DISTINCT f.segment, f.user_id
    FROM funnel_eligible f
    JOIN orders o ON o.user_id = f.user_id AND o.created_at >= f.touch_at
    {exclude_o}
),
paid_u AS (
    SELECT f.segment, f.user_id, p.amount_rub
    FROM funnel_eligible f
    JOIN orders o ON o.user_id = f.user_id
        AND o.status = 'paid'
        AND o.paid_at >= f.touch_at
    JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
    {exclude_o}
)
SELECT
    (SELECT COUNT(*)::int FROM cohort_touches) AS clicks_total,
    (SELECT COUNT(*)::int FROM tagged WHERE segment = 'client') AS clients,
    (SELECT COUNT(*)::int FROM tagged WHERE segment = 'first') AS first_cnt,
    (SELECT COUNT(*)::int FROM tagged WHERE segment = 'repeat') AS repeat_cnt,
    (SELECT COUNT(*)::int FROM ai_u WHERE segment = 'first') AS first_ai,
    (SELECT COUNT(*)::int FROM ai_u WHERE segment = 'repeat') AS repeat_ai,
    (SELECT COUNT(*)::int FROM ord_u WHERE segment = 'first') AS first_ordered,
    (SELECT COUNT(*)::int FROM ord_u WHERE segment = 'repeat') AS repeat_ordered,
    (SELECT COUNT(DISTINCT user_id)::int FROM paid_u WHERE segment = 'first') AS first_paid,
    (SELECT COUNT(DISTINCT user_id)::int FROM paid_u WHERE segment = 'repeat') AS repeat_paid,
    (SELECT COALESCE(SUM(amount_rub), 0)::float FROM paid_u WHERE segment = 'first') AS first_revenue,
    (SELECT COALESCE(SUM(amount_rub), 0)::float FROM paid_u WHERE segment = 'repeat') AS repeat_revenue
"""


@dataclass(frozen=True)
class SegmentFunnel:
    clicks: int = 0
    ai_dialog: int = 0
    ordered: int = 0
    paid: int = 0
    revenue: float = 0.0


@dataclass
class BibliaClubCampaignRow:
    campaign_id: int
    name: str
    status: str
    scheduled_at: Optional[datetime]
    campaign_source: str
    audience_size: int
    audience_sent: int
    sent_count: int
    failed_count: int
    blocked_count: int
    start_keys: List[str] = field(default_factory=list)
    clicks_total: int = 0
    clients_excluded: int = 0
    first: SegmentFunnel = field(default_factory=SegmentFunnel)
    repeat: SegmentFunnel = field(default_factory=SegmentFunnel)


@dataclass(frozen=True)
class BibliaClubCampaignReport:
    period_from: datetime
    period_to: datetime
    bot_username: str
    campaigns: tuple[BibliaClubCampaignRow, ...]


def extract_start_keys_from_campaign(
    text: Optional[str],
    buttons: Any,
    *,
    bot_username: Optional[str] = None,
) -> List[str]:
    """Извлекает ``start``-ключи из текста и JSON-кнопок рассылки."""
    keys: list[str] = []
    username = (bot_username or "").lstrip("@").lower()

    def _add(raw: str) -> None:
        key = (raw or "").strip()
        if key and key not in keys:
            keys.append(key)

    for blob in (text or "",):
        for mo in _START_URL_RE.finditer(blob):
            url_bot = mo.group(0).lower()
            if username and username not in url_bot:
                continue
            _add(mo.group(1))

    btn_data = buttons
    if isinstance(btn_data, str):
        try:
            btn_data = json.loads(btn_data)
        except json.JSONDecodeError:
            btn_data = None
    if isinstance(btn_data, list):
        for item in btn_data:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "")
            for mo in _START_URL_RE.finditer(url):
                url_bot = mo.group(0).lower()
                if username and username not in url_bot:
                    continue
                _add(mo.group(1))
    return keys


def biblia_db_configured(config: Any) -> bool:
    return bool(
        getattr(config, "BIBLIA_DB_NAME", "")
        and getattr(config, "BIBLIA_DB_USER", "")
    )


async def create_biblia_pool(config: Any) -> "asyncpg.Pool":
    import asyncpg

    return await asyncpg.create_pool(
        host=config.BIBLIA_DB_HOST,
        port=int(config.BIBLIA_DB_PORT or 5432),
        database=config.BIBLIA_DB_NAME,
        user=config.BIBLIA_DB_USER,
        password=config.BIBLIA_DB_PASSWORD,
        min_size=1,
        max_size=2,
        command_timeout=60,
    )


async def fetch_biblia_club_campaigns(
    biblia_pool: "asyncpg.Pool",
    *,
    bot_username: str,
    days_back: int = 30,
    days_forward: int = 3,
) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    period_from = now - timedelta(days=days_back)
    period_to = now + timedelta(days=days_forward)
    needle = (bot_username or "Talk_God_Bot").lstrip("@")
    async with biblia_pool.acquire() as conn:
        rows = await conn.fetch(
            _BIBLIA_CAMPAIGNS_SQL,
            period_from,
            period_to,
            needle,
        )
    return [dict(r) for r in rows]


async def _club_funnel_for_campaign(
    club_pool: "asyncpg.Pool",
    *,
    anchor_at: datetime,
    touch_keys: Sequence[str],
) -> Dict[str, Any]:
    if not touch_keys:
        return {}
    ex_at, ex_ids = sql_exclude_users("at.user_id", start_param=3)
    ex_o = ex_at.replace("at.user_id", "o.user_id")
    query = _CLUB_FUNNEL_SQL.format(exclude_at=ex_at, exclude_o=ex_o)
    async with club_pool.acquire() as conn:
        row = await conn.fetchrow(query, anchor_at, list(touch_keys), *ex_ids)
    return dict(row or {})


async def collect_biblia_club_campaign_report(
    club_pool: "asyncpg.Pool",
    biblia_pool: "asyncpg.Pool",
    *,
    bot_username: str,
    days_back: int = 30,
    days_forward: int = 3,
) -> BibliaClubCampaignReport:
    now = datetime.now(timezone.utc)
    period_from = now - timedelta(days=days_back)
    period_to = now + timedelta(days=days_forward)
    username = (bot_username or "Talk_God_Bot").lstrip("@")

    raw_campaigns = await fetch_biblia_club_campaigns(
        biblia_pool,
        bot_username=username,
        days_back=days_back,
        days_forward=days_forward,
    )

    campaigns: list[BibliaClubCampaignRow] = []
    for raw in raw_campaigns:
        keys = extract_start_keys_from_campaign(
            raw.get("text"),
            raw.get("buttons"),
            bot_username=username,
        )
        anchor = raw.get("scheduled_at")
        funnel: Dict[str, Any] = {}
        if keys and anchor:
            funnel = await _club_funnel_for_campaign(
                club_pool,
                anchor_at=anchor,
                touch_keys=keys,
            )

        row = BibliaClubCampaignRow(
            campaign_id=int(raw["id"]),
            name=str(raw.get("name") or ""),
            status=str(raw.get("status") or ""),
            scheduled_at=anchor,
            campaign_source=str(raw.get("campaign_source") or ""),
            audience_size=int(raw.get("audience_size") or 0),
            audience_sent=int(raw.get("audience_sent") or 0),
            sent_count=int(raw.get("sent_count") or 0),
            failed_count=int(raw.get("failed_count") or 0),
            blocked_count=int(raw.get("blocked_count") or 0),
            start_keys=keys,
            clicks_total=int(funnel.get("clicks_total") or 0),
            clients_excluded=int(funnel.get("clients") or 0),
            first=SegmentFunnel(
                clicks=int(funnel.get("first_cnt") or 0),
                ai_dialog=int(funnel.get("first_ai") or 0),
                ordered=int(funnel.get("first_ordered") or 0),
                paid=int(funnel.get("first_paid") or 0),
                revenue=float(funnel.get("first_revenue") or 0),
            ),
            repeat=SegmentFunnel(
                clicks=int(funnel.get("repeat_cnt") or 0),
                ai_dialog=int(funnel.get("repeat_ai") or 0),
                ordered=int(funnel.get("repeat_ordered") or 0),
                paid=int(funnel.get("repeat_paid") or 0),
                revenue=float(funnel.get("repeat_revenue") or 0),
            ),
        )
        campaigns.append(row)

    return BibliaClubCampaignReport(
        period_from=period_from,
        period_to=period_to,
        bot_username=username,
        campaigns=tuple(campaigns),
    )


def _pct(part: int, whole: int) -> str:
    if whole <= 0:
        return "—"
    return f"{100.0 * part / whole:.1f}%"


def _money(rub: float) -> str:
    return f"{int(round(rub)):,}".replace(",", " ") + " ₽"


_DAILY_BLOCK_LEGEND = (
    "<i>📬 доставлено в Библии · 🔗 переход в клуб · 💳 оплаты</i>"
)


def _campaign_totals(report: BibliaClubCampaignReport) -> Dict[str, int | float]:
    first_clicks = sum(c.first.clicks for c in report.campaigns)
    repeat_clicks = sum(c.repeat.clicks for c in report.campaigns)
    first_paid = sum(c.first.paid for c in report.campaigns)
    repeat_paid = sum(c.repeat.paid for c in report.campaigns)
    first_rev = sum(c.first.revenue for c in report.campaigns)
    repeat_rev = sum(c.repeat.revenue for c in report.campaigns)
    return {
        "sent": sum(c.audience_sent for c in report.campaigns),
        "clicks": first_clicks + repeat_clicks,
        "first_clicks": first_clicks,
        "repeat_clicks": repeat_clicks,
        "paid": first_paid + repeat_paid,
        "first_paid": first_paid,
        "repeat_paid": repeat_paid,
        "revenue": first_rev + repeat_rev,
        "first_revenue": first_rev,
        "repeat_revenue": repeat_rev,
    }


def _fmt_segment_block(label: str, seg: SegmentFunnel) -> List[str]:
    if seg.clicks <= 0:
        return [f"<b>{html_mod.escape(label)}</b>: 0 переходов"]
    return [
        f"<b>{html_mod.escape(label)}</b> · {seg.clicks} перешли",
        f"  → ИИ: {seg.ai_dialog} ({_pct(seg.ai_dialog, seg.clicks)})",
        f"  → заказ: {seg.ordered} ({_pct(seg.ordered, seg.ai_dialog)})",
        f"  → оплата: {seg.paid} ({_pct(seg.paid, seg.ordered)}) · {_money(seg.revenue)}",
        f"  сквозная CR: {_pct(seg.paid, seg.clicks)}",
    ]


def format_biblia_club_campaign_html(report: BibliaClubCampaignReport) -> str:
    p_from = report.period_from.astimezone(MSK).strftime("%d.%m.%Y")
    p_to = report.period_to.astimezone(MSK).strftime("%d.%m.%Y")
    lines = [
        "<b>📖→🤖 Библия → Клуб: кампании</b>",
        f"<i>@{html_mod.escape(report.bot_username)} · {p_from} — {p_to} (МСК)</i>",
        "<i>Когорта клуба: переход по ключу из ссылки после времени рассылки. "
        "Клиенты с активной лицензией в воронку не входят.</i>",
        "",
    ]

    if not report.campaigns:
        lines.append("Нет рассылок с ссылкой на клубный бот в выбранном окне.")
        return "\n".join(lines)

    for c in report.campaigns:
        sched = c.scheduled_at.astimezone(MSK).strftime("%d.%m.%Y %H:%M") if c.scheduled_at else "—"
        keys_s = ", ".join(
            f"<code>{html_mod.escape(k)}</code>" for k in c.start_keys
        ) or "—"
        lines.append(
            f"<b>{html_mod.escape(c.name)}</b> · id <code>{c.campaign_id}</code> · "
            f"{html_mod.escape(c.status)}"
        )
        lines.append(f"📅 {sched} · ключи: {keys_s}")
        lines.append(
            f"📬 Библия: аудитория {c.audience_size:,} · доставлено {c.audience_sent:,} "
            f"(sent {c.sent_count:,} / fail {c.failed_count:,})".replace(",", " ")
        )
        lines.append(
            f"🔗 Клуб: переходов {c.clicks_total}"
            + (f" · уже клиенты {c.clients_excluded}" if c.clients_excluded else "")
        )
        lines.extend(_fmt_segment_block("Впервые", c.first))
        lines.extend(_fmt_segment_block("Повторно", c.repeat))
        lines.append("")

    return "\n".join(lines).strip()


def format_biblia_club_daily_block(report: Optional[BibliaClubCampaignReport]) -> str:
    """Компактный блок для ежедневного отчёта v2 (топ кампаний + итого)."""
    if not report or not report.campaigns:
        return ""

    totals = _campaign_totals(report)

    lines = [
        "<b>📖→🤖 Библия → Клуб</b>",
        f"<i>Рассылки с ссылкой на @{html_mod.escape(report.bot_username)} · 30 дн. + 3 дн.</i>",
        _DAILY_BLOCK_LEGEND,
        f"• Кампаний: {len(report.campaigns)} · доставлено в Библии: {int(totals['sent']):,}".replace(
            ",", " "
        ),
        (
            f"• Переходов в клуб: {int(totals['clicks'])} "
            f"(впервые {int(totals['first_clicks'])} · повторно {int(totals['repeat_clicks'])})"
        ),
        (
            f"• Оплат: {int(totals['paid'])} · {_money(float(totals['revenue']))} "
            f"(впервые {int(totals['first_paid'])} · {_money(float(totals['first_revenue']))} · "
            f"повторно {int(totals['repeat_paid'])} · {_money(float(totals['repeat_revenue']))})"
        ),
        "",
    ]

    for c in report.campaigns[:8]:
        sched = c.scheduled_at.astimezone(MSK).strftime("%d.%m") if c.scheduled_at else "—"
        keys = c.start_keys[0] if c.start_keys else "?"
        name = html_mod.escape(c.name.strip() or "—")
        paid_total = c.first.paid + c.repeat.paid
        rev_total = c.first.revenue + c.repeat.revenue
        lines.append(
            f"• {sched} <b>{name}</b> · <code>{html_mod.escape(keys)}</code>: "
            f"📬{c.audience_sent} → 🔗{c.clicks_total} "
            f"(вп {c.first.clicks} / повт {c.repeat.clicks}) → "
            f"💳{paid_total} ({_money(rev_total)})"
        )

    if len(report.campaigns) > 8:
        lines.append(f"<i>…ещё {len(report.campaigns) - 8} кампаний — /biblia_club</i>")
    else:
        lines.append("<i>Подробно: /biblia_club</i>")

    return "\n".join(lines)
