"""Форматирование блока отчёта по выводу легаси 103 → stuck_dialog."""

from __future__ import annotations

from typing import Any, Dict, Optional


def format_legacy_reactivation_block(stats: Optional[Dict[str, Any]]) -> str:
    if not stats:
        return ""
    migrated_total = int(stats.get("migrated_total") or 0)
    if migrated_total == 0 and int(stats.get("remaining") or 0) == 0:
        return ""

    remaining = int(stats.get("remaining") or 0)
    ping_total = int(stats.get("ping_sent_total") or 0)
    reacted_total = int(stats.get("reacted_total") or 0)
    migrated_y = int(stats.get("migrated_yesterday") or 0)
    ping_y = int(stats.get("ping_sent_yesterday") or 0)
    reacted_y = int(stats.get("reacted_yesterday") or 0)

    react_pct = f"{100.0 * reacted_total / ping_total:.1f}%" if ping_total else "—"
    react_y_pct = f"{100.0 * reacted_y / ping_y:.1f}%" if ping_y else "—"

    lines = [
        "<b>🔄 Вывод легаси 103 → stuck_dialog</b>",
        f"• Осталось в очереди: <b>{remaining}</b>",
        f"• Выведено всего: <b>{migrated_total}</b> (пинг: <b>{ping_total}</b>)",
        f"• Отреагировали всего: <b>{reacted_total}</b> ({react_pct})",
        f"• Вчера выведено: <b>{migrated_y}</b> (пинг: <b>{ping_y}</b>, "
        f"реакция: <b>{reacted_y}</b>, {react_y_pct})",
    ]
    return "\n".join(lines)
