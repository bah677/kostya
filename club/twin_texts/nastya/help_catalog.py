"""Каталог команд для /help."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Tuple

from bot.texts.ru_help_club import (
    HELP_DIGEST_TEST_DESCRIPTION,
    HELP_SCRIPTURE_PULSE_TEST_DESCRIPTION,
)

HelpTier = Literal["user", "admin", "superadmin"]

TIER_LABELS = {
    "user": "Пользователь",
    "admin": "Админ",
    "superadmin": "Супер-админ",
}

TIER_ORDER: Tuple[HelpTier, ...] = ("user", "admin", "superadmin")

SECTION_TITLES = {
    "user": "Для всех пользователей",
    "admin": "Дополнительно для админов",
    "superadmin": "Только супер-админ",
}

HELP_FOOTER_USER = (
    "Команды админов недоступны. Если вы администратор — "
    "ваш Telegram ID должен быть в таблице admins."
)

HELP_FOOTER_ADMIN = (
    "Админ-команды работают в личке с ботом и в админ-супергруппе "
    "(кроме /new_mailing — только личка)."
)


@dataclass(frozen=True)
class HelpEntry:
    command: str
    description: str
    tier: HelpTier


HELP_CATALOG: Tuple[HelpEntry, ...] = (
    HelpEntry("/start", "Начало работы с ботом, онбординг", "user"),
    HelpEntry("/payment", "Тарифы и оплата подписки", "user"),
    HelpEntry("/subs", "Статус подписки и продление", "user"),
    HelpEntry("/club", "Доступ в группу клуба (в личке и в группе клуба)", "user"),
    HelpEntry("/support", "Обращение в поддержку (тикет)", "user"),
    HelpEntry("/feedback", "Отзыв о боте / клубе", "user"),
    HelpEntry("/affiliate", "Партнёрская (реферальная) ссылка", "user"),
    HelpEntry("/benefit", "Бонусы и материалы по акциям", "user"),
    HelpEntry("/help", "Список команд по вашему уровню доступа", "user"),
    HelpEntry("/menu", "Меню возможностей бота (инлайн-кнопки)", "user"),
    HelpEntry(
        "—",
        "Обычные сообщения в личке — диалог с ИИ-ассистентом (не команда)",
        "user",
    ),
    HelpEntry("/admin", "Краткая справка админ-консоли (алиас /adm)", "admin"),
    HelpEntry(
        "/report",
        "Отчёт в личку; <code>metrics</code> — без DeepSeek, <code>--no-v2</code> — только legacy",
        "admin",
    ),
    HelpEntry(
        "/clear_my_chat",
        "Удалить свою переписку с ботом в личке (кнопка-подтверждение; алиас /clear_dm)",
        "admin",
    ),
    HelpEntry("/churn, /otval", "Отчёт по оттоку + анализ DeepSeek в личку", "admin"),
    HelpEntry("/graf", "График метрики: выбор показателя и периода → PNG", "admin"),
    HelpEntry(
        "/td",
        "Конверсия тест-драйва (ТД): период 7–120 дн. → отчёт в личку",
        "admin",
    ),
    HelpEntry(
        "/excluded",
        "Отвалившиеся (просрочка): профиль оплат и комбинации тарифов → в личку",
        "admin",
    ),
    HelpEntry(
        "/gift",
        "Лицензия в подарок: /gift USER_ID [дней] (клуб «Любящие Бога»)",
        "admin",
    ),
    HelpEntry(
        "/mailing_funnel",
        "Воронка внутренних рассылок (ID из mailing_campaigns): без аргументов — каталог; с ID — метрики",
        "admin",
    ),
    HelpEntry(
        "/ref_funnel",
        "Воронка внешних ref-кампаний: без аргументов — каталог; KEY или type:/search: — метрики (алиас /campaign_funnel)",
        "admin",
    ),
    HelpEntry(
        "/followup_leads",
        "Лиды без лицензии: 3 цепочки ×2 (не писали / писали / заказ) + финалы (алиас /dozhim_leads)",
        "admin",
    ),
    HelpEntry("/digest_test", HELP_DIGEST_TEST_DESCRIPTION, "admin"),
    HelpEntry("/scripture_pulse_test", HELP_SCRIPTURE_PULSE_TEST_DESCRIPTION, "admin"),
    HelpEntry(
        "—",
        "Карта автоматических дожимов и напоминаний — docs/FOLLOWUP_MAP.md",
        "admin",
    ),
    HelpEntry("/new_mailing", "Мастер создания рассылки (только в личке)", "admin"),
    HelpEntry("/cancel", "Отмена мастера рассылки", "admin"),
    HelpEntry("/done", "Завершить загрузку медиа в мастере рассылки", "admin"),
    HelpEntry("/code_id", "Получить file_id медиа для рассылок", "admin"),
    HelpEntry(
        "reply в топике поддержки",
        "Ответ на пост с номером тикета → ответ пользователю, тикет закрывается",
        "admin",
    ),
    HelpEntry(
        "reply в топике продаж",
        "Ответ на карточку с User ID → сообщение в личку пользователю",
        "admin",
    ),
    HelpEntry("/admins", "Список Telegram ID из таблицы admins", "superadmin"),
    HelpEntry("/admin_add", "Добавить админа: /admin_add USER_ID [note]", "superadmin"),
    HelpEntry("/admin_del", "Удалить админа: /admin_del USER_ID", "superadmin"),
)

HELP_TITLE_HTML = "<b>📖 Справка по командам</b>"
