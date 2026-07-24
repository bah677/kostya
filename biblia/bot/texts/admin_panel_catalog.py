"""Полный каталог команд библии для /adm (ревизия по register Command)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Literal, Tuple

HelpTier = Literal["user", "admin", "superadmin"]

TIER_LABELS = {
    "user": "Пользователь",
    "admin": "Админ",
    "superadmin": "Супер-админ",
}

ADMIN_GROUP_ORDER: Tuple[str, ...] = (
    "users",
    "reports",
    "mailings",
    "marathon",
    "tools",
    "notes",
)

ADMIN_GROUP_TITLES: Dict[str, str] = {
    "users": "👤 Для пользователей",
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


# Все команды из command_handlers + features (Command(...))
ADMIN_CATALOG: Tuple[AdminEntry, ...] = (
    # —— пользователи ——
    AdminEntry("/start", "Приветствие и онбординг", "user", "users"),
    AdminEntry(
        "/support",
        "Обращение в поддержку (тикет)",
        "user",
        "users",
    ),
    AdminEntry(
        "/payment, /donat",
        "Меню поддержки проекта (донаты)",
        "user",
        "users",
    ),
    AdminEntry(
        "/affiliate",
        "Персональная реферальная ссылка и кратко о бонусах",
        "user",
        "users",
    ),
    AdminEntry(
        "/refstats, /refs, /myrefs",
        "Статистика по своей реферальной ссылке",
        "user",
        "users",
    ),
    AdminEntry("/feedback", "Отзыв / обратная связь", "user", "users"),
    AdminEntry(
        "/prayer, /molitva",
        "Персональная молитва",
        "user",
        "users",
    ),
    AdminEntry(
        "/challenge, /chellenge",
        "Скрипчурный челлендж",
        "user",
        "users",
    ),
    AdminEntry(
        "/challenge_cancel",
        "Отменить участие в челлендже",
        "user",
        "users",
    ),
    AdminEntry(
        "/more",
        "Частые вопросы / готовые запросы",
        "user",
        "users",
    ),
    AdminEntry(
        "—",
        "Обычные сообщения в личке — диалог по Писанию (не команда)",
        "user",
        "users",
    ),
    # —— отчёты (админ) ——
    AdminEntry(
        "/report",
        "Суточный админ-отчёт в личку",
        "admin",
        "reports",
    ),
    AdminEntry(
        "/refstats USER_ID",
        "Статистика рефералов другого пользователя: "
        "<code>/refstats 123</code> или <code>/refstats ref_123</code> (только админ)",
        "admin",
        "reports",
    ),
    # —— рассылки ——
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
    # —— марафон ——
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
    # —— инструменты ——
    AdminEntry(
        "/adm, /admin",
        "Админ-панель: разделы с командами",
        "admin",
        "tools",
    ),
    # —— подсказки ——
    AdminEntry(
        "reply в топике поддержки",
        "Ответ на пост с номером тикета → ответ пользователю",
        "admin",
        "notes",
    ),
)


def _tier_rank(tier: HelpTier) -> int:
    order = ("user", "admin", "superadmin")
    return order.index(tier)


def entries_for_tier(viewer_tier: HelpTier) -> List[AdminEntry]:
    max_rank = _tier_rank(viewer_tier)
    return [e for e in ADMIN_CATALOG if _tier_rank(e.tier) <= max_rank]


def groups_for_tier(viewer_tier: HelpTier) -> List[str]:
    present = {e.group for e in entries_for_tier(viewer_tier)}
    return [g for g in ADMIN_GROUP_ORDER if g in present]
