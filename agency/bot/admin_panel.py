"""UI /adm для agency — полный список команд + разделы."""

from __future__ import annotations

import html as html_mod
from typing import List, Optional, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.adm_catalog import (
    ADM_CATALOG,
    ADMIN_GROUP_ORDER,
    ADMIN_GROUP_TITLES,
    command_entries,
    entries_for_group,
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


def _format_entries(entries: List) -> str:
    lines: List[str] = []
    for e in entries:
        if e.command == "—":
            lines.append(f"• <i>{e.description}</i>")
        else:
            lines.append(
                f"• <code>{html_mod.escape(e.command)}</code> — {e.description}"
            )
    return "\n".join(lines) if lines else "—"


def build_admin_panel_home() -> Tuple[str, InlineKeyboardMarkup]:
    """Главная: все команды сразу, снизу кнопки разделов."""
    parts = [
        "<b>🛠 Админ-панель (agency)</b>",
        "",
        "<b>Все команды</b>",
    ]
    for key in ADMIN_GROUP_ORDER:
        title = ADMIN_GROUP_TITLES.get(key, key)
        group_entries = entries_for_group(key)
        if not group_entries:
            continue
        parts.append("")
        parts.append(f"<b>{html_mod.escape(title)}</b>")
        parts.append(_format_entries(group_entries))

    n_cmd = len(command_entries())
    parts.append("")
    parts.append(f"<i>Команд: {n_cmd} · записей каталога: {len(ADM_CATALOG)}</i>")

    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for key in ADMIN_GROUP_ORDER:
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


def build_admin_panel_group(group_key: str) -> Tuple[str, InlineKeyboardMarkup]:
    title = ADMIN_GROUP_TITLES.get(group_key, group_key)
    text = (
        f"<b>{html_mod.escape(title)}</b>\n\n"
        f"{_format_entries(entries_for_group(group_key))}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data=CB_HOME)]
        ]
    )
    return text, kb
