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
from bot.services.prayer_usage_report import POWER_USER_MIN_PRAYERS

CB_PREFIX = "apnl"
CB_HOME = f"{CB_PREFIX}:h"
CB_GROUP_PREFIX = f"{CB_PREFIX}:g:"
CB_QUICK_PREFIX = f"{CB_PREFIX}:q:"
CB_QUICK_MAIL3 = f"{CB_QUICK_PREFIX}mail3"
CB_QUICK_PRAYER = f"{CB_QUICK_PREFIX}prayer"
CB_QUICK_LIVE = f"{CB_QUICK_PREFIX}live"
# Молитвы: apnl:pr:{screen}:{period}  period=7|30|all|yday|YYYY-MM-DD
# Запрос даты: apnl:pr:ask:{screen}
CB_PRAYER_PREFIX = f"{CB_PREFIX}:pr:"
CB_PRAYER_ASK_PREFIX = f"{CB_PRAYER_PREFIX}ask:"

QUICK_REPORT_KEYS = {
    "mail3": CB_QUICK_MAIL3,
    "prayer": CB_QUICK_PRAYER,
    "live": CB_QUICK_LIVE,
}

_PRAYER_SCREENS = ("ov", "dn", "an", "pw", "cmp")
_PRAYER_PERIODS = ("7", "30", "all", "yday")


def admin_panel_cb_group(group_key: str) -> str:
    return f"{CB_GROUP_PREFIX}{group_key}"


def parse_admin_panel_group_cb(data: str) -> Optional[str]:
    if not data.startswith(CB_GROUP_PREFIX):
        return None
    key = data[len(CB_GROUP_PREFIX) :].strip()
    return key if key in ADMIN_GROUP_TITLES else None


def parse_admin_panel_quick_cb(data: str) -> Optional[str]:
    if not data.startswith(CB_QUICK_PREFIX):
        return None
    key = data[len(CB_QUICK_PREFIX) :].strip()
    return key if key in QUICK_REPORT_KEYS else None


def prayer_stats_cb(screen: str, period: str) -> str:
    return f"{CB_PRAYER_PREFIX}{screen}:{period}"


def prayer_stats_ask_date_cb(screen: str) -> str:
    return f"{CB_PRAYER_ASK_PREFIX}{screen}"


def parse_prayer_stats_ask_cb(data: str) -> Optional[str]:
    """Экран, для которого ждём дату, или None."""
    if not data.startswith(CB_PRAYER_ASK_PREFIX):
        return None
    screen = data[len(CB_PRAYER_ASK_PREFIX) :].strip()
    return screen if screen in _PRAYER_SCREENS and screen != "cmp" else None


def parse_prayer_stats_cb(data: str) -> Optional[Tuple[str, str]]:
    """Возвращает (screen, period) или None."""
    from bot.services.prayer_usage_report import parse_prayer_period_token

    if data == CB_QUICK_PRAYER:
        return "ov", "30"
    if not data.startswith(CB_PRAYER_PREFIX):
        return None
    if data.startswith(CB_PRAYER_ASK_PREFIX):
        return None
    rest = data[len(CB_PRAYER_PREFIX) :].strip()
    parts = rest.split(":")
    if len(parts) != 2:
        return None
    screen, period_raw = parts[0].strip(), parts[1].strip()
    if screen not in _PRAYER_SCREENS:
        return None
    period = parse_prayer_period_token(period_raw)
    if not period:
        return None
    return screen, period


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


def _quick_report_rows() -> List[List[InlineKeyboardButton]]:
    return [
        [
            InlineKeyboardButton(
                text="🚦 Статус деплоя",
                callback_data=CB_QUICK_LIVE,
            )
        ],
        [
            InlineKeyboardButton(
                text="📨 Последние 3 рассылки",
                callback_data=CB_QUICK_MAIL3,
            )
        ],
        [
            InlineKeyboardButton(
                text="🙏 Статистика молитв",
                callback_data=prayer_stats_cb("ov", "30"),
            )
        ],
    ]


def build_admin_panel_home(
    viewer_tier: HelpTier,
) -> Tuple[str, InlineKeyboardMarkup]:
    groups = groups_for_tier(viewer_tier)
    parts = [
        "<b>🛠 Админ-панель (библия)</b>",
        f"<i>Уровень: {html_mod.escape(TIER_LABELS[viewer_tier])}</i>",
        "",
        "<b>⚡ Быстрые отчёты</b> — статус на сейчас.",
        "",
        "Ниже — разделы со списком команд.",
        "",
        f"<i>{html_mod.escape(HELP_FOOTER)}</i>",
    ]
    rows: List[List[InlineKeyboardButton]] = list(_quick_report_rows())
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
    kb_rows: List[List[InlineKeyboardButton]] = []
    if group_key == "reports":
        kb_rows.extend(_quick_report_rows())
    elif group_key == "mailings":
        kb_rows.append(
            [
                InlineKeyboardButton(
                    text="📨 Последние 3 рассылки",
                    callback_data=CB_QUICK_MAIL3,
                )
            ]
        )
    kb_rows.append([InlineKeyboardButton(text="« Назад", callback_data=CB_HOME)])
    return text, InlineKeyboardMarkup(inline_keyboard=kb_rows)


def build_quick_report_keyboard_for(key: str) -> InlineKeyboardMarkup:
    refresh_cb = QUICK_REPORT_KEYS.get(key, CB_HOME)
    if key == "prayer":
        return build_prayer_stats_keyboard("ov", "30")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data=refresh_cb)],
            [InlineKeyboardButton(text="« Назад", callback_data=CB_HOME)],
        ]
    )


def build_prayer_stats_keyboard(screen: str, period: str) -> InlineKeyboardMarkup:
    from bot.services.prayer_usage_report import parse_prayer_period_token

    screen = screen if screen in _PRAYER_SCREENS else "ov"
    period_norm = parse_prayer_period_token(period) or "30"
    screen_labels = {
        "ov": "Обзор",
        "dn": "Донаты",
        "an": "Аномалии",
        "pw": f"≥{POWER_USER_MIN_PRAYERS}",
        "cmp": "Сравн.",
    }
    period_labels = {"7": "7 дн", "30": "30 дн", "all": "Всё", "yday": "Вчера"}
    row1 = [
        InlineKeyboardButton(
            text=("• " if s == screen else "") + screen_labels[s],
            callback_data=prayer_stats_cb(s, period_norm),
        )
        for s in ("ov", "dn", "cmp")
    ]
    row2 = [
        InlineKeyboardButton(
            text=("• " if s == screen else "") + screen_labels[s],
            callback_data=prayer_stats_cb(s, period_norm),
        )
        for s in ("an", "pw")
    ]
    rows: list = [row1, row2]
    if screen != "cmp":
        rows.append(
            [
                InlineKeyboardButton(
                    text=("• " if p == period_norm else "") + period_labels[p],
                    callback_data=prayer_stats_cb(screen, p),
                )
                for p in ("7", "30", "all", "yday")
            ]
        )
        date_mark = "• " if len(period_norm) == 10 and period_norm[4] == "-" else ""
        date_label = (
            f"{date_mark}📅 {period_norm[8:10]}.{period_norm[5:7]}.{period_norm[0:4]}"
            if date_mark
            else "📅 Дата…"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=date_label,
                    callback_data=prayer_stats_ask_date_cb(screen),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔄 Обновить",
                callback_data=prayer_stats_cb(screen, period_norm),
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=CB_HOME)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
