"""Каталог команд для /adm — полный список зарегистрированных Command()."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

ADMIN_GROUP_ORDER = ("ops", "memory", "access", "info")
ADMIN_GROUP_TITLES: Dict[str, str] = {
    "ops": "Запуски",
    "memory": "Память / KPI",
    "access": "Доступ",
    "info": "Справка",
}


@dataclass(frozen=True)
class AdmEntry:
    command: str
    description: str
    group: str


# Все команды из bot/handlers_admin.py (включая алиасы).
ADM_CATALOG: Tuple[AdmEntry, ...] = (
    # ops
    AdmEntry("/agency_run", "Прогон Bible Bot Manager за вчера (с LLM)", "ops"),
    AdmEntry("/agency_run_nums", "Только KPI / цифры, без LLM", "ops"),
    # memory
    AdmEntry(
        "/agency_recs",
        "Открытые рекомендации + кнопки accept / reject / ship / abandon",
        "memory",
    ),
    AdmEntry("/agency_gaps", "Открытые пробелы данных", "memory"),
    # access (супер)
    AdmEntry("/admins", "Список админов БД + супер/env (только супер)", "access"),
    AdmEntry(
        "/admin_add",
        "Добавить админа: /admin_add &lt;telegram_id&gt; [note] (только супер)",
        "access",
    ),
    AdmEntry(
        "/admin_del",
        "Удалить админа: /admin_del &lt;telegram_id&gt; (только супер)",
        "access",
    ),
    AdmEntry(
        "—",
        "Супер: SUPER_ADMIN_ID в .env. Обычные админы — таблица admins (/admin_add).",
        "access",
    ),
    # info
    AdmEntry("/adm", "Админ-панель (это меню)", "info"),
    AdmEntry("/admin", "Алиас /adm", "info"),
    AdmEntry("/help", "Короткий список команд", "info"),
    AdmEntry("/start", "Алиас /help", "info"),
    AdmEntry(
        "—",
        "Реплай на daily brief — обсуждение отчёта (Claude/GPT-4o), можно пересобрать рекомендации",
        "info",
    ),
    AdmEntry(
        "—",
        "Ночной cron 03:00 МСК → brief в AGENCY_BRIEF_CHAT_ID",
        "info",
    ),
    AdmEntry(
        "—",
        "Чаты: личка с ботом или AGENCY_BRIEF_CHAT_ID",
        "info",
    ),
)


def entries_for_group(group_key: str) -> List[AdmEntry]:
    return [e for e in ADM_CATALOG if e.group == group_key]


def command_entries() -> List[AdmEntry]:
    """Только реальные команды (без заметок «—»)."""
    return [e for e in ADM_CATALOG if e.command != "—"]
