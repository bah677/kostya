"""UI админ-панели: /adm с разбивкой по группам (инлайн-кнопки)."""

from __future__ import annotations

import html as html_mod
from typing import List, Optional, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.texts.admin_panel_groups import ADMIN_GROUP_ORDER, ADMIN_GROUP_TITLES
from bot.texts.help_catalog import (
    HELP_CATALOG,
    HELP_FOOTER_ADMIN,
    TIER_LABELS,
    TIER_ORDER,
    HelpEntry,
    HelpTier,
)

CB_PREFIX = "apnl"
CB_HOME = f"{CB_PREFIX}:h"
CB_GROUP_PREFIX = f"{CB_PREFIX}:g:"


def admin_panel_cb_group(group_key: str) -> str:
    return f"{CB_GROUP_PREFIX}{group_key}"


def parse_admin_panel_group_cb(data: str) -> Optional[str]:
    if not data.startswith(CB_GROUP_PREFIX):
        return None
    key = data[len(CB_GROUP_PREFIX) :].strip()
    return key if key in ADMIN_GROUP_TITLES else None


def _tier_rank(tier: HelpTier) -> int:
    return TIER_ORDER.index(tier)


def _entries_for_viewer(viewer_tier: HelpTier) -> List[HelpEntry]:
    max_rank = _tier_rank(viewer_tier)
    return [e for e in HELP_CATALOG if _tier_rank(e.tier) <= max_rank]


def _admin_entries(viewer_tier: HelpTier) -> List[HelpEntry]:
    visible = _entries_for_viewer(viewer_tier)
    return [e for e in visible if e.group and e.tier in ("admin", "superadmin")]


def _groups_for_viewer(viewer_tier: HelpTier) -> List[str]:
    entries = _admin_entries(viewer_tier)
    present = {e.group for e in entries if e.group}
    if viewer_tier != "superadmin":
        present.discard("access")
    return [g for g in ADMIN_GROUP_ORDER if g in present]


def _format_entries(entries: List[HelpEntry]) -> str:
    lines: List[str] = []
    for e in entries:
        if e.command == "—":
            lines.append(f"• <i>{e.description}</i>")
        else:
            lines.append(
                f"• <code>{html_mod.escape(e.command)}</code> — {e.description}"
            )
    return "\n".join(lines)


def build_admin_panel_home(
    viewer_tier: HelpTier,
    *,
    report_hint: str = "",
) -> Tuple[str, InlineKeyboardMarkup]:
    groups = _groups_for_viewer(viewer_tier)
    parts = [
        "<b>🛠 Админ-панель (club)</b>",
        f"<i>Уровень: {html_mod.escape(TIER_LABELS[viewer_tier])}</i>",
        "",
        "Выберите раздел — внутри список команд.",
    ]
    if report_hint:
        parts.extend(["", report_hint])
    parts.extend(["", f"<i>{html_mod.escape(HELP_FOOTER_ADMIN)}</i>"])

    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for key in groups:
        title = ADMIN_GROUP_TITLES.get(key, key)
        row.append(
            InlineKeyboardButton(text=title, callback_data=admin_panel_cb_group(key))
        )
        if len(row) >= 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    return "\n".join(parts), InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_panel_group(
    viewer_tier: HelpTier,
    group_key: str,
) -> Tuple[str, InlineKeyboardMarkup]:
    title = ADMIN_GROUP_TITLES.get(group_key, group_key)
    entries = [
        e
        for e in _admin_entries(viewer_tier)
        if e.group == group_key
        and (
            e.tier != "superadmin"
            or _tier_rank(viewer_tier) >= _tier_rank("superadmin")
        )
    ]
    if group_key == "access" and viewer_tier != "superadmin":
        entries = []

    body = _format_entries(entries) if entries else "— в этом разделе нет команд"
    text = f"<b>{html_mod.escape(title)}</b>\n\n{body}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data=CB_HOME)]
        ]
    )
    return text, kb


def build_admin_console_help_html(
    viewer_tier: HelpTier,
    *,
    report_hint: str = "",
) -> str:
    """Текстовый fallback (тесты / без кнопок): все группы подряд."""
    parts = [
        "<b>🛠 Админ-панель (club)</b>",
        f"<i>Уровень: {html_mod.escape(TIER_LABELS[viewer_tier])}</i>",
        "<i>В боте удобнее: /adm → кнопки разделов</i>",
    ]
    if report_hint:
        parts.extend(["", report_hint])
    for key in _groups_for_viewer(viewer_tier):
        title = ADMIN_GROUP_TITLES.get(key, key)
        entries = [e for e in _admin_entries(viewer_tier) if e.group == key]
        if not entries:
            continue
        parts.extend(["", f"<b>{html_mod.escape(title)}</b>", _format_entries(entries)])
    parts.extend(["", f"<i>{html_mod.escape(HELP_FOOTER_ADMIN)}</i>"])
    return "\n".join(parts)
