"""HTML-форматирование отчёта v2."""

from __future__ import annotations

import html as html_mod
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Dict, List, Optional


_CHANNEL_LABELS = {
    "tg": "Telegram",
    "biblia_bot": "Библия бот",
    "other": "Другое",
}


def _channel_label(raw: Any) -> str:
    if raw is None or str(raw).strip() in ("", "None"):
        return "без канала"
    s = str(raw).strip()
    return _CHANNEL_LABELS.get(s, s)


def _pct(part: int, whole: int) -> str:
    if not whole:
        return "—"
    return f"{100.0 * part / whole:.1f}%"


def _money(n: float) -> str:
    return f"{n:,.0f} ₽".replace(",", " ")


def _int(n: int) -> str:
    return f"{int(n):,}".replace(",", " ")


def _format_funnel_block(f: Dict[str, Any]) -> str:
    starts = int(f.get("starts", 0) or 0)
    with_lic = int(f.get("starts_with_active_license", 0) or 0)
    no_lic = int(f.get("starts_without_active_license", 0) or 0)
    return (
        f"• /start: {starts} (без лицензии: {no_lic}, уже клиент: {with_lic})\n"
        f"• → ИИ-диалог: {f.get('ai_dialogs', 0)} ({f.get('cr_ai', '—')})\n"
        f"• → заказ: {f.get('orders', 0)} ({f.get('cr_order', '—')})\n"
        f"• → оплата: {f.get('paid', 0)} ({f.get('cr_paid', '—')})\n"
        f"• Сквозная CR: {f.get('cr_total', '—')}"
    )


def _format_paid_new_renewal(breakdown: Dict[str, Any]) -> str:
    if not breakdown:
        return ""
    new_b = breakdown.get("new") or {}
    ren_b = breakdown.get("renewal") or {}
    new_amt = float(new_b.get("total_amount") or 0)
    ren_amt = float(ren_b.get("total_amount") or 0)
    new_cnt = int(new_b.get("count") or 0)
    ren_cnt = int(ren_b.get("count") or 0)
    return (
        f"•• Новые: {_money(new_amt)} ({new_cnt} {_orders_word(new_cnt)})\n"
        f"•• Продления: {_money(ren_amt)} ({ren_cnt} {_orders_word(ren_cnt)})"
    )


def _orders_word(n: int) -> str:
    n = abs(int(n))
    if 11 <= (n % 100) <= 14:
        return "заказов"
    r = n % 10
    if r == 1:
        return "заказ"
    if 2 <= r <= 4:
        return "заказа"
    return "заказов"


def _signed_delta(delta: Optional[float]) -> str:
    if delta is None:
        return ""
    d = float(delta)
    if abs(d) < 0.5:
        return ""
    if d > 0:
        return f" +{_money(d)}"
    return f" −{_money(-d)}"


def _format_tariff_table(title: str, breakdown: Dict[str, Dict]) -> str:
    if not breakdown:
        return f"<i>{html_mod.escape(title)}</i>\n• нет оплат"
    lines = [f"<i>{html_mod.escape(title)}</i>"]
    for name, data in sorted(
        breakdown.items(),
        key=lambda x: (-int((x[1] or {}).get("orders") or 0), x[0]),
    ):
        orders = int((data or {}).get("orders") or 0)
        users = int((data or {}).get("unique_users") or 0)
        amount = float((data or {}).get("amount") or 0)
        lines.append(
            f"• {html_mod.escape(str(name))}: {orders} "
            f"({users} чел.) · {_money(amount)}"
        )
    return "\n".join(lines)


def _format_monthly_revenue(m: Dict[str, Any]) -> str:
    rows = m.get("monthly_revenue") or []
    if not rows:
        return "• нет данных"
    lines: List[str] = []
    for row in rows:
        month = str(row.get("month") or "?")
        orders = int(row.get("orders_count") or 0)
        amount = float(row.get("total_amount") or 0)
        is_cur = bool(row.get("is_current_month"))
        delta_s = "" if is_cur else _signed_delta(row.get("delta_amount"))
        lines.append(
            f"• {html_mod.escape(month)}: {_money(amount)} "
            f"({orders} {_orders_word(orders)}){delta_s}"
        )
    total = sum(float(row.get("total_amount") or 0) for row in rows)
    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📊 <b>ИТОГО:</b> {_money(total)}")
    return "\n".join(lines)


def _format_finance_tariffs(m: Dict[str, Any]) -> str:
    parts = [
        _format_tariff_table("Вчера", m.get("tariff_breakdown") or {}),
        _format_tariff_table("30 дней", m.get("tariff_breakdown_30d") or {}),
        _format_tariff_table("Всё время", m.get("tariff_breakdown_all") or {}),
    ]
    return "\n\n".join(parts)


def _format_audience_summary(a: Dict[str, Any]) -> str:
    if not a:
        return "• нет данных"
    total = int(a.get("total") or 0)
    active = int(a.get("active") or 0)
    active_leads = int(a.get("active_leads") or 0)
    active_clients = int(a.get("active_clients") or 0)
    blocked = int(a.get("blocked") or 0)
    return (
        f"• Всего в боте: <b>{_int(total)}</b>\n"
        f"• Доступен бот: <b>{_int(active)}</b> "
        f"(клиенты {_int(active_clients)}, лиды {_int(active_leads)})\n"
        f"• Заблокировали: <b>{_int(blocked)}</b>"
    )


def _format_risk_summary(m: Dict[str, Any]) -> str:
    s = m.get("expiring_risk_summary") or {}
    if not s:
        return "• нет данных"

    total = int(s.get("total_silent") or 0)
    if total == 0:
        return "• нет клиентов в зоне риска (≤7 дн. + молчат в группе 14 дн.)"

    d13 = int(s.get("days_1_3") or 0)
    d47 = int(s.get("days_4_7") or 0)
    return "\n".join(
        [
            f"• <b>Итого к контакту:</b> {total} чел. (лицензия ≤7 дн., в группе молчат 14+ дн.)",
            f"• 1–3 дня до конца: {d13} чел.",
            f"• 4–7 дней: {d47} чел.",
        ]
    )


def _format_campaigns(rows: List[Dict[str, Any]], *, limit: int = 12) -> str:
    if not rows:
        return "• нет данных (нужен backfill attribution)"

    by_channel: dict[str, list] = defaultdict(list)
    for row in rows[:limit]:
        ch = _channel_label(row.get("channel_type"))
        by_channel[ch].append(row)

    parts: List[str] = []
    for ch_name in sorted(by_channel.keys(), key=lambda c: (-sum(int(r.get("revenue") or 0) for r in by_channel[c]), c)):
        parts.append(f"<b>📂 {html_mod.escape(ch_name)}</b>")
        for i, row in enumerate(by_channel[ch_name], 1):
            name = str(row.get("name") or row.get("ref_key") or "?")
            entered = int(row.get("entered") or 0)
            ai = int(row.get("ai_dialog") or 0)
            ordered = int(row.get("ordered") or 0)
            paid = int(row.get("paid") or 0)
            rev = float(row.get("revenue") or 0)
            parts.append(
                f"{i}) <b>{html_mod.escape(name)}</b>\n"
                f"   {entered} → {ai} → {ordered} → {paid} · {_money(rev)}\n"
                f"   CR: ИИ {_pct(ai, entered)} · заказ {_pct(ordered, ai)} · "
                f"оплата {_pct(paid, ordered)} · сквозная {_pct(paid, entered)}"
            )
        parts.append("")
    return "\n".join(parts).strip()


def _format_channels(rows: List[Dict[str, Any]], overlap: Dict[str, Any]) -> str:
    if not rows:
        return "• нет данных (нужен backfill attribution)"

    lines: List[str] = [
        "<i>lifetime; «зашли» — уник. user с касанием канала (один человек может учитываться в нескольких строках).</i>",
        "",
    ]
    sum_entered = 0
    for row in rows:
        ch = _channel_label(row.get("channel_type"))
        entered = int(row.get("entered") or 0)
        paid = int(row.get("paid") or 0)
        rev = float(row.get("revenue") or 0)
        sum_entered += entered
        lines.append(
            f"• <b>{html_mod.escape(ch)}</b>: зашли {entered}, оплата {paid}, "
            f"{_money(rev)} (CR {_pct(paid, entered)})"
        )

    total = int(overlap.get("total_users") or 0) if overlap else 0
    if total and sum_entered > total:
        lines.append("")
        lines.append(
            f"<i>Сумма «зашли» по каналам: {sum_entered:,} · уник. людей: {total:,} "
            f"(пересечение +{sum_entered - total:,}).</i>".replace(",", " ")
        )

    if not total:
        return "\n".join(lines)

    single = int(overlap.get("single_channel") or 0)
    two = int(overlap.get("two_channels") or 0)
    three = int(overlap.get("three_channels") or 0)
    four_plus = int(overlap.get("four_plus_channels") or 0)
    multi = int(overlap.get("multi_channel") or 0)

    lines.extend(
        [
            "",
            f"<b>🔀 Пересечение каналов</b> (уник. user: {total:,})".replace(",", " "),
            f"• Только 1 канал: {single} ({_pct(single, total)})",
            f"• Ровно 2 канала: {two} ({_pct(two, total)})",
        ]
    )
    if three:
        lines.append(f"• Ровно 3 канала: {three} ({_pct(three, total)})")
    if four_plus:
        lines.append(f"• 4+ канала: {four_plus} ({_pct(four_plus, total)})")
    lines.append(f"• Итого в 2+ каналах: {multi} ({_pct(multi, total)})")

    return "\n".join(lines)


def format_message_clients_finance(m: Dict[str, Any]) -> str:
    paid = m.get("paid_orders") or {}
    paid_amt = float(paid.get("total_amount", 0) if isinstance(paid, dict) else 0)
    comps = m.get("comparisons") or {}
    snaps = comps.get("snapshots") or {}

    def snap_amt(d: date) -> float | None:
        s = snaps.get(str(d))
        if not s:
            return None
        return float(s.get("total_amount") or 0)

    yday = date.today() - timedelta(days=1)
    d2 = yday - timedelta(days=1)
    d7 = yday - timedelta(days=7)
    try:
        if yday.month > 1:
            d_month = yday.replace(month=yday.month - 1)
        else:
            d_month = yday.replace(year=yday.year - 1, month=12)
    except ValueError:
        d_month = yday - timedelta(days=28)

    cmp_parts = []
    for label, d in (("D-1", d2), ("D-7", d7), ("мес", d_month)):
        prev = snap_amt(d)
        if prev is not None:
            diff = paid_amt - prev
            cmp_parts.append(f"{label}: {'+' if diff >= 0 else ''}{diff:,.0f} ₽")
    cmp_s = f" ({'; '.join(cmp_parts)})" if cmp_parts else ""

    legacy_exp = m.get("users_expiring") or []
    exp_lines = [
        f"• {int(row['days_left'])} дн.: {int(row['user_count'])} чел."
        for row in legacy_exp
    ]
    exp_text = "\n".join(exp_lines) if exp_lines else "• нет"

    paid_split = _format_paid_new_renewal(m.get("paid_breakdown") or {})
    paid_split_block = f"\n{paid_split}" if paid_split else ""

    return f"""<b>📊 КЛУБ — Клиенты и финансы</b>
<i>{html_mod.escape(str(m.get('period', '')))}</i>

<b>I. 👥 Клиенты</b>
• Активных лицензий: {int(m.get('active_licenses', 0)):,}
• 💬 В группе вчера: {int(m.get('club_group_active_yesterday', 0)):,}
• 🤐 Молчуны в группе: {int(m.get('group_silent_count', 0)):,}
• 📤 Истекли вчера: {int(m.get('expired_yesterday', 0)):,}

<b>⚠️ Риск продления</b>
{_format_risk_summary(m)}

<b>🔴 Просрочено всего:</b> {int(m.get('users_expired', 0)):,} чел.

<b>⏳ Истекают (7 дней)</b>
{exp_text}

<b>II. 💰 Оплаты (вчера)</b>
• Сумма оплат: {paid_amt:,.0f} ₽{cmp_s}
{paid_split_block}

<b>📦 Тарифы</b>
{_format_finance_tariffs(m)}

<b>💰 Всего выручка проекта</b>
{_format_monthly_revenue(m)}

<i>🤖 Аналитика DeepSeek — отдельным сообщением ниже.</i>"""


def _format_benefit3_deeplink_section(m: Dict[str, Any]) -> str:
    report = m.get("benefit3_deeplink")
    if not report:
        return ""
    from bot.services.benefit3_deeplink_report import format_benefit3_deeplink_block

    block = format_benefit3_deeplink_block(report)
    return "\n\n" + block if block else ""


def _format_biblia_club_section(m: Dict[str, Any]) -> str:
    report = m.get("biblia_club_campaigns")
    if not report:
        return ""
    from bot.services.biblia_club_campaign_report import format_biblia_club_daily_block

    block = format_biblia_club_daily_block(report)
    return "\n\n" + block if block else ""


def _format_legacy_reactivation_section(m: Dict[str, Any]) -> str:
    stats = m.get("legacy_103_reactivation")
    if not stats:
        return ""
    from bot.services.legacy_reactivation_report import format_legacy_reactivation_block

    block = format_legacy_reactivation_block(stats)
    return f"\n\n{block}" if block else ""


def _format_followup_leads_section(m: Dict[str, Any]) -> str:
    fu = m.get("followup_leads")
    if fu is None:
        return ""
    from bot.services.followup_leads_report import format_followup_leads_block

    return "\n\n" + format_followup_leads_block(fu, for_daily=True)


def format_message_leads_metrics(m: Dict[str, Any]) -> str:
    f = m.get("funnel_72h") or {}
    f_new = m.get("funnel_72h_new_users") or {}
    ai = m.get("ai_agent") or {}

    aud = m.get("audience_summary") or {}

    return f"""<b>📈 КЛУБ — Лиды и кампании</b>
<i>{html_mod.escape(str(m.get('period', '')))}</i>

<b>I. 👤 База (lifetime)</b>
<i>Все, кто хоть раз писал боту; «клиент» = активная лицензия сейчас.</i>
{_format_audience_summary(aud)}

<b>II. 🔄 Воронка (72ч, все /start)</b>
<i>Включая действующих и бывших клиентов; заказ/оплата после этого /start.</i>
{_format_funnel_block(f)}

<b>III. ✨ Воронка 72ч — новые</b>
<i>Первый /start в жизни за последние 72 ч; шаги после этого /start.</i>
{_format_funnel_block(f_new)}

<b>🤖 ИИ-агент</b>
• Медиана сообщений до 1-й оплаты: {ai.get('median_user_msgs_before_pay', 0):.1f}
• Среднее: {ai.get('avg_user_msgs_before_pay', 0):.1f}

<b>📣 Кампании (lifetime)</b>
{_format_campaigns(m.get('campaigns_by_ref') or [])}

<b>📡 Каналы (lifetime)</b>
{_format_channels(m.get('campaigns_by_channel') or [], m.get('channel_overlap') or {})}
{_format_followup_leads_section(m)}
{_format_benefit3_deeplink_section(m)}
{_format_biblia_club_section(m)}
{_format_legacy_reactivation_section(m)}

<i>🤖 Заключения DeepSeek — отдельными сообщениями ниже.</i>"""


def format_message_llm_group(m: Dict[str, Any]) -> str:
    body = (m.get("llm") or {}).get("group") or ""
    return (
        f"<b>🤖 DeepSeek — группа клуба</b>\n"
        f"<i>{html_mod.escape(str(m.get('period', '')))}</i>\n\n"
        f"{body or '<i>нет анализа</i>'}"
    )


def format_message_llm_leads(m: Dict[str, Any]) -> str:
    body = (m.get("llm") or {}).get("leads") or ""
    return (
        f"<b>🤖 DeepSeek — лиды и продажи</b>\n"
        f"<i>{html_mod.escape(str(m.get('period', '')))}</i>\n\n"
        f"{body or '<i>нет анализа</i>'}"
    )


def build_v2_report_messages(
    m: Dict[str, Any], *, include_llm: bool = True
) -> List[str]:
    """Порядок: метрики → DeepSeek (в конце, если include_llm)."""
    out = [
        format_message_clients_finance(m),
        format_message_leads_metrics(m),
    ]
    if not include_llm:
        return out
    llm = m.get("llm") or {}
    if llm.get("group"):
        out.append(format_message_llm_group(m))
    if llm.get("leads"):
        out.append(format_message_llm_leads(m))
    return out


# Обратная совместимость
def format_message_leads(m: Dict[str, Any]) -> str:
    parts = [format_message_leads_metrics(m)]
    llm = m.get("llm") or {}
    if llm.get("leads"):
        parts.append(format_message_llm_leads(m))
    return "\n\n".join(parts)
