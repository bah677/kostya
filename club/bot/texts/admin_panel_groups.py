"""Группы команд админ-панели (/adm)."""

from __future__ import annotations

from typing import Dict, Tuple

# Порядок кнопок на главном экране /adm
ADMIN_GROUP_ORDER: Tuple[str, ...] = (
    "reports",
    "funnels",
    "mailings",
    "outreach",
    "tools",
    "access",
    "notes",
)

ADMIN_GROUP_TITLES: Dict[str, str] = {
    "reports": "📊 Отчёты",
    "funnels": "📈 Воронки и ключи",
    "mailings": "📨 Рассылки и промо",
    "outreach": "📬 Дайджест и outreach",
    "tools": "🛠 Инструменты",
    "access": "🔑 Доступ",
    "notes": "ℹ️ Подсказки",
}
