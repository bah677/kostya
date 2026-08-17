"""UI админ-панели аватара: /adm с разбивкой по группам."""

from __future__ import annotations

import html as html_mod
from typing import List, Optional, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.texts.admin_panel_catalog import (
    ADMIN_GROUP_ORDER,
    ADMIN_GROUP_TITLES,
    HELP_FOOTER,
    TIER_LABELS,
    AdminEntry,
    HelpTier,
    entries_for_tier,
)

CB_PREFIX = "apnl"
CB_HOME = f"{CB_PREFIX}:h"
CB_GROUP_PREFIX = f"{CB_PREFIX}:g:"
LEDGER_OPEN_CB = "ldg:home"


def admin_panel_cb_group(group_key: str) -> str:
    return f"{CB_GROUP_PREFIX}{group_key}"


def parse_admin_panel_group_cb(data: str) -> Optional[str]:
    if not data.startswith(CB_GROUP_PREFIX):
        return None
    key = data[len(CB_GROUP_PREFIX) :].strip()
    return key if key in ADMIN_GROUP_TITLES else None


def _panel_entries(viewer_tier: HelpTier) -> List[AdminEntry]:
    return [e for e in entries_for_tier(viewer_tier) if e.group]


def _groups_for_viewer(viewer_tier: HelpTier) -> List[str]:
    present = {e.group for e in _panel_entries(viewer_tier)}
    if viewer_tier in ("admin", "superadmin"):
        present.add("money")
    if viewer_tier != "superadmin":
        present.discard("access")
    return [g for g in ADMIN_GROUP_ORDER if g in present]


def _format_entries(entries: List[AdminEntry]) -> str:
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
) -> Tuple[str, InlineKeyboardMarkup]:
    groups = _groups_for_viewer(viewer_tier)
    parts = [
        "<b>🛠 Админ-панель (аватар)</b>",
        f"<i>Уровень: {html_mod.escape(TIER_LABELS[viewer_tier])}</i>",
        "",
        "Выберите раздел — внутри список команд.",
        "",
        f"<i>{html_mod.escape(HELP_FOOTER)}</i>",
    ]
    rows: List[List[InlineKeyboardButton]] = []
    if "money" in groups:
        rows.append(
            [
                InlineKeyboardButton(
                    text="💸 Расходы",
                    callback_data=LEDGER_OPEN_CB,
                )
            ]
        )
        groups = [g for g in groups if g != "money"]
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
    entries = [e for e in _panel_entries(viewer_tier) if e.group == group_key]
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
