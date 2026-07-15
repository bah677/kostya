"""Ночной отчёт по расходу LLM-токенов."""

from __future__ import annotations

import html as html_mod
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")


def _esc(s: Any) -> str:
    return html_mod.escape(str(s or ""))


def _fmt_int(n: Any) -> str:
    try:
        return f"{int(n or 0):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "0"


def yesterday_msk() -> date:
    """Календарный «вчера» по Europe/Moscow (как ночной отчёт вовлечённости)."""
    return datetime.now(MSK).date() - timedelta(days=1)


async def build_llm_token_report_html(
    user_storage,
    *,
    report_date: Optional[date] = None,
) -> str:
    ref = report_date or yesterday_msk()
    stats: Dict[str, Any] = await user_storage.get_global_token_stats_for_msk_date(ref)
    total = stats.get("total") or {}
    by_kind: List[Dict[str, Any]] = stats.get("by_provider_request_model") or []
    req_count = int(total.get("total_requests") or 0)

    lines = [
        f"<b>🪙 LLM-токены · {ref.strftime('%d.%m.%Y')}</b>",
        f"<i>Календарный день по МСК (00:00–23:59)</i>",
        "",
        f"Запросов: <b>{_fmt_int(total.get('total_requests'))}</b>",
        f"Уникальных user_id: <b>{_fmt_int(total.get('unique_users'))}</b>",
        f"Prompt: <b>{_fmt_int(total.get('total_prompt_tokens'))}</b>",
        f"Completion: <b>{_fmt_int(total.get('total_completion_tokens'))}</b>",
        f"Всего: <b>{_fmt_int(total.get('total_tokens'))}</b>",
        "",
        "<b>По request_kind / model:</b>",
    ]

    if not by_kind:
        if req_count == 0:
            lines.append("— нет записей в token_usage за этот день")
        else:
            lines.append("— нет разбивки по kind/model")
    else:
        for row in by_kind[:25]:
            kind = _esc(row.get("request_kind") or "?")
            model = _esc(row.get("model") or "?")
            prov = _esc(row.get("provider") or "?")
            tok = _fmt_int(row.get("total_tokens"))
            cnt = _fmt_int(row.get("request_count"))
            lines.append(f"• {prov} / <code>{kind}</code> / {model}: {tok} tok ({cnt} req)")

    top = stats.get("top_users") or []
    if top:
        lines.extend(["", "<b>Топ-5 user_id по токенам:</b>"])
        for row in top[:5]:
            uid = row.get("user_id")
            name = _esc(row.get("first_name") or row.get("username") or uid)
            lines.append(
                f"• {name} (<code>{uid}</code>): {_fmt_int(row.get('total_tokens'))} tok"
            )

    lines.extend(
        [
            "",
            "<i>Не попадают в token_usage (пока): batch без user_storage, часть legacy _chat.</i>",
        ]
    )
    return "\n".join(lines)
