"""Каталог команд аватара для /adm."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Literal, Tuple

HelpTier = Literal["user", "admin", "superadmin"]

TIER_LABELS = {
    "user": "Пользователь",
    "admin": "Админ",
    "superadmin": "Супер-админ",
}

TIER_ORDER: Tuple[HelpTier, ...] = ("user", "admin", "superadmin")

ADMIN_GROUP_ORDER: Tuple[str, ...] = (
    "users",
    "money",
    "rag",
    "telemost",
    "ydisk",
    "shorts",
    "youtube",
    "voice",
    "access",
    "notes",
)

ADMIN_GROUP_TITLES: Dict[str, str] = {
    "users": "👤 Для пользователей",
    "money": "💸 Расходы",
    "rag": "📚 RAG / база",
    "telemost": "📧 Телемост",
    "ydisk": "☁ Яндекс.Диск",
    "shorts": "🎬 Шортсы / записи",
    "youtube": "▶ YouTube-молитвы",
    "voice": "🎙 Voicebox",
    "access": "🔑 Доступ",
    "notes": "ℹ️ Подсказки",
}

HELP_FOOTER = (
    "Админ-команды — в личке с ботом "
    "(кроме /rag_backfill и карточек в рабочих топиках)."
)


@dataclass(frozen=True)
class AdminEntry:
    command: str
    description: str
    tier: HelpTier
    group: str


ADMIN_CATALOG: Tuple[AdminEntry, ...] = (
    AdminEntry("/start", "Приветствие", "user", "users"),
    AdminEntry("/help", "Справка", "user", "users"),
    AdminEntry("/new", "Новая задача с аватаром", "user", "users"),
    AdminEntry(
        "/summary, /svodka",
        "Что уже в базе (продукты, типы, фрагменты)",
        "user",
        "users",
    ),
    AdminEntry("/support", "Поддержка", "user", "users"),
    AdminEntry("/feedback", "Обратная связь", "user", "users"),
    AdminEntry("/payment, /donat", "Донат", "user", "users"),
    AdminEntry("/affiliate", "Реферальная ссылка", "user", "users"),
    AdminEntry(
        "/expenses, /rashody",
        "то же меню расходов, что кнопка «💸 Расходы»",
        "admin",
        "notes",
    ),
    AdminEntry(
        "/code_id",
        "Узнать file_id вложения (для настройки)",
        "user",
        "users",
    ),
    AdminEntry(
        "/adm, /admin",
        "Админ-панель (это меню)",
        "admin",
        "notes",
    ),
    AdminEntry(
        "/rag_topics",
        "Список топиков RAG-группы",
        "admin",
        "rag",
    ),
    AdminEntry(
        "/rag_clear",
        "Полная очистка Chroma (материалы + золотой фонд)",
        "superadmin",
        "rag",
    ),
    AdminEntry(
        "/rag_backfill",
        "Догрузка старых материалов (в админской ветке группы)",
        "admin",
        "rag",
    ),
    AdminEntry(
        "/telemost_status",
        "Статус почты Телемоста → RAG",
        "admin",
        "telemost",
    ),
    AdminEntry(
        "/telemost_poll",
        "Опросить почту Телемоста вручную",
        "admin",
        "telemost",
    ),
    AdminEntry(
        "/telemost_load №",
        "Повторно показать карточку загрузки",
        "admin",
        "telemost",
    ),
    AdminEntry(
        "/telemost_unload №",
        "Откатить загрузку в RAG (чанки + кэш)",
        "admin",
        "telemost",
    ),
    AdminEntry(
        "/ydisk_status",
        "Статус синхронизации Яндекс.Диска",
        "admin",
        "ydisk",
    ),
    AdminEntry(
        "/ydisk_sync",
        "Синхронизировать Диск вручную",
        "admin",
        "ydisk",
    ),
    AdminEntry(
        "/shorts_cut",
        "Видео-шортсы (если включены)",
        "admin",
        "shorts",
    ),
    AdminEntry(
        "/audio_cut [№]",
        "Аудио-шортсы → голосовые (топик шортсов)",
        "admin",
        "shorts",
    ),
    AdminEntry(
        "/full_voice №",
        "Выложить полную запись встречи",
        "admin",
        "shorts",
    ),
    AdminEntry(
        "—",
        "В топике: «нарезать шортцы …» / «выложить полную запись встречи …»",
        "admin",
        "shorts",
    ),
    AdminEntry(
        "/yt_prayer",
        "Собрать 3×16:9 + 9 шортсов (тренды) → топик",
        "admin",
        "youtube",
    ),
    AdminEntry(
        "/yt_prayer force",
        "Пересобрать за сегодня (игнор done.json)",
        "admin",
        "youtube",
    ),
    AdminEntry(
        "/voice_sample [имя]",
        "Создать модель Voicebox (sample ≤30с)",
        "admin",
        "voice",
    ),
    AdminEntry(
        "/voice_add_sample [имя|id]",
        "Добавить sample в существующую модель",
        "admin",
        "voice",
    ),
    AdminEntry(
        "/voice_test [текст]",
        "Озвучить текст → голосовое TG",
        "admin",
        "voice",
    ),
    AdminEntry(
        "/voice_models",
        "Список моделей Voicebox",
        "admin",
        "voice",
    ),
    AdminEntry(
        "/admin_add <id>",
        "Добавить админа бота",
        "superadmin",
        "access",
    ),
    AdminEntry(
        "/admin_block <id>",
        "Снять админа",
        "superadmin",
        "access",
    ),
)


def entries_for_tier(viewer_tier: HelpTier) -> List[AdminEntry]:
    max_rank = TIER_ORDER.index(viewer_tier)
    return [e for e in ADMIN_CATALOG if TIER_ORDER.index(e.tier) <= max_rank]
