"""Каталог админ-команд для /adm (библия)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Literal, Tuple

HelpTier = Literal["admin", "superadmin"]

TIER_LABELS = {
    "admin": "Админ",
    "superadmin": "Супер-админ",
}

ADMIN_GROUP_ORDER: Tuple[str, ...] = (
    "reports",
    "mailings",
    "marathon",
    "tools",
    "notes",
)

ADMIN_GROUP_TITLES: Dict[str, str] = {
    "reports": "📊 Отчёты",
    "mailings": "📨 Рассылки",
    "marathon": "🕯 Марафон донатов",
    "tools": "🛠 Инструменты",
    "notes": "ℹ️ Подсказки",
}

HELP_FOOTER = (
    "Админ-команды — в личке с ботом "
    "(кроме ответов на тикеты в админ-группе)."
)


@dataclass(frozen=True)
class AdminEntry:
    command: str
    description: str
    tier: HelpTier
    group: str


ADMIN_CATALOG: Tuple[AdminEntry, ...] = (
    AdminEntry(
        "/adm, /admin",
        "Админ-панель: разделы с командами",
        "admin",
        "tools",
    ),
    AdminEntry(
        "/report",
        "Суточный отчёт в личку",
        "admin",
        "reports",
    ),
    AdminEntry(
        "/new_mailing",
        "Мастер рассылки (только личка): медиа, /done, /cancel",
        "admin",
        "mailings",
    ),
    AdminEntry("/cancel", "Отмена мастера рассылки", "admin", "mailings"),
    AdminEntry(
        "/done",
        "Завершить загрузку медиа в мастере рассылки",
        "admin",
        "mailings",
    ),
    AdminEntry(
        "/code_id",
        "Получить file_id медиа для рассылок",
        "admin",
        "mailings",
    ),
    AdminEntry(
        "/marathon",
        "Статус активного марафона донатов",
        "admin",
        "marathon",
    ),
    AdminEntry(
        "/marathon_start",
        "Создать марафон (имя → цель → описание)",
        "admin",
        "marathon",
    ),
    AdminEntry(
        "/marathon_stop",
        "Остановить активный марафон",
        "admin",
        "marathon",
    ),
    AdminEntry(
        "/marathon_crypto",
        "Вручную учесть крипто-донат в марафоне",
        "admin",
        "marathon",
    ),
    AdminEntry(
        "/marathon_backfill",
        "Ретроспектива марафона: /marathon_backfill N",
        "admin",
        "marathon",
    ),
    AdminEntry(
        "reply в топике поддержки",
        "Ответ на пост с номером тикета → ответ пользователю",
        "admin",
        "notes",
    ),
)


def entries_for_tier(viewer_tier: HelpTier) -> List[AdminEntry]:
    if viewer_tier == "superadmin":
        return list(ADMIN_CATALOG)
    return [e for e in ADMIN_CATALOG if e.tier == "admin"]


def groups_for_tier(viewer_tier: HelpTier) -> List[str]:
    present = {e.group for e in entries_for_tier(viewer_tier)}
    return [g for g in ADMIN_GROUP_ORDER if g in present]
