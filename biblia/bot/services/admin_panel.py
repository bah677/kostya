"""UI админ-панели библии: /adm с разбивкой по группам."""

from __future__ import annotations

import html as html_mod
from typing import List, Optional, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.texts.admin_panel_catalog import (
    ADMIN_GROUP_TITLES,
    HELP_FOOTER,
    TIER_LABELS,
    AdminEntry,
    HelpTier,
    entries_for_tier,
    groups_for_tier,
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


def _format_entries(entries: List[AdminEntry]) -> str:
    lines: List[str] = []
    for e in entries:
        lines.append(
            f"• <code>{html_mod.escape(e.command)}</code> — {e.description}"
        )
    return "\n".join(lines)


def build_admin_panel_home(
    viewer_tier: HelpTier,
) -> Tuple[str, InlineKeyboardMarkup]:
    groups = groups_for_tier(viewer_tier)
    parts = [
        "<b>🛠 Админ-панель (библия)</b>",
        f"<i>Уровень: {html_mod.escape(TIER_LABELS[viewer_tier])}</i>",
        "",
        "Выберите раздел — внутри список команд.",
        "",
        f"<i>{html_mod.escape(HELP_FOOTER)}</i>",
    ]
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
    entries = [e for e in entries_for_tier(viewer_tier) if e.group == group_key]
    body = _format_entries(entries) if entries else "— в этом разделе нет команд"
    text = f"<b>{html_mod.escape(title)}</b>\n\n{body}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data=CB_HOME)]
        ]
    )
    return text, kb
