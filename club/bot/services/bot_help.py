"""Справка по командам бота в зависимости от уровня доступа."""

from __future__ import annotations

import html as html_mod
from typing import List, Optional

from bot.admin_guard import is_telegram_admin
from bot.texts.help_catalog import (
    HELP_CATALOG,
    HELP_FOOTER_ADMIN,
    HELP_FOOTER_USER,
    HELP_TITLE_HTML,
    SECTION_TITLES,
    TIER_LABELS,
    TIER_ORDER,
    HelpEntry,
    HelpTier,
)
from config import config
from storage.user_storage import UserStorage


async def resolve_help_tier(
    user_storage: UserStorage, telegram_user_id: int
) -> HelpTier:
    sid = int(getattr(config, "SUPER_ADMIN_ID", 0) or 0)
    if sid and telegram_user_id == sid:
        return "superadmin"
    if await is_telegram_admin(user_storage, telegram_user_id):
        return "admin"
    return "user"


def _tier_rank(tier: HelpTier) -> int:
    return TIER_ORDER.index(tier)


def _entries_for_viewer(viewer_tier: HelpTier) -> List[HelpEntry]:
    max_rank = _tier_rank(viewer_tier)
    return [e for e in HELP_CATALOG if _tier_rank(e.tier) <= max_rank]


def _format_section(title: str, entries: List[HelpEntry]) -> str:
    if not entries:
        return ""
    lines = [f"<b>{html_mod.escape(title)}</b>"]
    for e in entries:
        if e.command == "—":
            lines.append(f"• <i>{e.description}</i>")
        else:
            lines.append(
                f"• <code>{html_mod.escape(e.command)}</code> — {e.description}"
            )
    return "\n".join(lines)


def build_help_html(viewer_tier: HelpTier, *, chat_hint: Optional[str] = None) -> str:
    visible = _entries_for_viewer(viewer_tier)
    by_tier: dict[HelpTier, List[HelpEntry]] = {t: [] for t in TIER_ORDER}
    for entry in visible:
        by_tier[entry.tier].append(entry)

    parts = [
        HELP_TITLE_HTML,
        f"<i>Уровень доступа: {html_mod.escape(TIER_LABELS[viewer_tier])}</i>",
    ]
    if chat_hint:
        parts.append(f"<i>{html_mod.escape(chat_hint)}</i>")

    for tier in TIER_ORDER:
        if _tier_rank(tier) > _tier_rank(viewer_tier):
            continue
        block = _format_section(SECTION_TITLES[tier], by_tier[tier])
        if block:
            parts.append("")
            parts.append(block)

    if viewer_tier == "user":
        parts.append("")
        parts.append(f"<i>{html_mod.escape(HELP_FOOTER_USER)}</i>")
    elif viewer_tier == "admin":
        parts.append("")
        parts.append(f"<i>{html_mod.escape(HELP_FOOTER_ADMIN)}</i>")

    return "\n".join(parts)


ADMIN_CONSOLE_TITLE_HTML = "<b>Админ-консоль (club)</b>"
ADMIN_CONSOLE_INTRO_HTML = (
    "<i>Полный список с учётом уровня доступа: <code>/help</code></i>"
)
ADMIN_CONSOLE_TOPICS_HTML = (
    "<b>Топики админ-группы</b>\n"
    "• <b>reply</b> на пост поддержки с номером тикета — ответ пользователю, тикет закрывается.\n"
    "• <b>reply</b> в топике диалога на карточку с User ID — сообщение в личку.\n"
    "• В персональных топиках ответы <b>клубного агента</b> помечены 🏠."
)
ADMIN_CONSOLE_LEGACY_NOTE_HTML = (
    "<i>Не перенесено с legacy:</i> /donation."
)


def build_admin_console_help_html(
    viewer_tier: HelpTier,
    *,
    report_hint: str = "",
) -> str:
    """Справка /admin и /adm из единого каталога команд."""
    visible = _entries_for_viewer(viewer_tier)
    admin_entries = [e for e in visible if e.tier == "admin"]
    super_entries = [e for e in visible if e.tier == "superadmin"]

    parts = [ADMIN_CONSOLE_TITLE_HTML, ADMIN_CONSOLE_INTRO_HTML]
    if report_hint:
        parts.append("")
        parts.append(report_hint)
    if admin_entries:
        parts.append("")
        parts.append(_format_section(SECTION_TITLES["admin"], admin_entries))
    if super_entries and viewer_tier == "superadmin":
        parts.append("")
        parts.append(_format_section(SECTION_TITLES["superadmin"], super_entries))
    parts.append("")
    parts.append(ADMIN_CONSOLE_TOPICS_HTML)
    parts.append("")
    parts.append(ADMIN_CONSOLE_LEGACY_NOTE_HTML)
    parts.append("")
    parts.append(f"<i>{html_mod.escape(HELP_FOOTER_ADMIN)}</i>")
    return "\n".join(parts)
