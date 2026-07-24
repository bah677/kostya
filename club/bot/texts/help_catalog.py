"""Каталог команд для /help и /adm (группы админ-панели)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Tuple

from bot.texts.ru_help_club import (
    HELP_DIGEST_TEST_DESCRIPTION,
    HELP_OUTREACH_DM_TEST_DESCRIPTION,
    HELP_OUTREACH_PILOT_REFRESH_DESCRIPTION,
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
    # Ключ группы для /adm (только admin/superadmin); None = только /help
    group: Optional[str] = None


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
    HelpEntry(
        "/admin, /adm",
        "Админ-панель: разделы с командами",
        "admin",
        "tools",
    ),
    HelpEntry(
        "/report",
        "Отчёт в личку; <code>metrics</code> — без DeepSeek; <code>--no-v2</code> — только legacy; <code>legacy</code> / <code>v2</code> — варианты",
        "admin",
        "reports",
    ),
    HelpEntry(
        "/biblia_club",
        "Отчёт воронки Библия → Клуб",
        "admin",
        "reports",
    ),
    HelpEntry(
        "/churn, /otval",
        "Отчёт по оттоку + анализ DeepSeek в личку",
        "admin",
        "reports",
    ),
    HelpEntry(
        "/graf",
        "График метрики: выбор показателя и периода → PNG в личку",
        "admin",
        "reports",
    ),
    HelpEntry(
        "/td",
        "Конверсия тест-драйва (ТД): период 7–120 дн. → отчёт в личку",
        "admin",
        "reports",
    ),
    HelpEntry(
        "/excluded",
        "Отвалившиеся (просрочка): профиль оплат по тарифам и комбинациям (7–90 дн. или всё время) → в личку",
        "admin",
        "reports",
    ),
    HelpEntry(
        "/mailing_funnel",
        "Воронка рассылок (ID из mailing_campaigns); алиас <code>/mail_funnel</code>",
        "admin",
        "funnels",
    ),
    HelpEntry(
        "/ref_funnel",
        "Воронка ref-кампаний: каталог или KEY / <code>type:</code> / <code>search:</code>; алиас <code>/campaign_funnel</code>",
        "admin",
        "funnels",
    ),
    HelpEntry(
        "/ref_key",
        "Очередь ref-ключей (диплинки): без аргумента — список; KEY — карточка и псевдоним",
        "admin",
        "funnels",
    ),
    HelpEntry(
        "/touch_key",
        "Колбэки оплаты/promo: без аргумента — список; CALLBACK — карточка и псевдоним",
        "admin",
        "funnels",
    ),
    HelpEntry(
        "/followup_leads",
        "Лиды без лицензии: 3 цепочки ×2 + финалы; алиас <code>/dozhim_leads</code>",
        "admin",
        "funnels",
    ),
    HelpEntry(
        "/new_mailing",
        "Мастер рассылки (только личка): медиа загрузками, <code>/done</code>, <code>/cancel</code>",
        "admin",
        "mailings",
    ),
    HelpEntry(
        "/new_promo",
        "Мастер промо-кампании (deep link <code>/start=promo_…</code>)",
        "admin",
        "mailings",
    ),
    HelpEntry("/cancel", "Отмена мастера рассылки или промо", "admin", "mailings"),
    HelpEntry(
        "/done",
        "Завершить загрузку медиа в мастере рассылки",
        "admin",
        "mailings",
    ),
    HelpEntry("/code_id", "Получить file_id медиа для рассылок", "admin", "mailings"),
    HelpEntry("—", "Новости и дайджест клубной группы", "admin", "outreach"),
    HelpEntry("/digest_test", HELP_DIGEST_TEST_DESCRIPTION, "admin", "outreach"),
    HelpEntry(
        "/scripture_pulse_test",
        HELP_SCRIPTURE_PULSE_TEST_DESCRIPTION,
        "admin",
        "outreach",
    ),
    HelpEntry(
        "/outreach_pilot_refresh",
        HELP_OUTREACH_PILOT_REFRESH_DESCRIPTION,
        "admin",
        "outreach",
    ),
    HelpEntry(
        "/outreach_dm_test",
        HELP_OUTREACH_DM_TEST_DESCRIPTION,
        "admin",
        "outreach",
    ),
    HelpEntry(
        "—",
        "Рассылки в личку (пилот): дайджест и цитаты — <code>CLUB_OUTREACH_DM_ENABLED</code>; "
        "в топик группы не публикуются, пока outreach DM включён",
        "admin",
        "outreach",
    ),
    HelpEntry(
        "—",
        "Автопубликация в топик группы: дайджест (<code>CLUB_DIGEST_ENABLED</code>) и "
        "цитаты (<code>CLUB_SCRIPTURE_PULSE_ENABLED</code>) — только если outreach DM выключен",
        "admin",
        "outreach",
    ),
    HelpEntry(
        "/schedule",
        "Расписание: week (по умолч.), <code>today</code>, <code>raw</code>, <code>2weeks</code>",
        "admin",
        "outreach",
    ),
    HelpEntry(
        "—",
        "Топик «Расписание» в админ-группе: правки нативным текстом; вечерний дайджест 20:00 МСК",
        "admin",
        "outreach",
    ),
    HelpEntry(
        "/gift",
        "Лицензия в подарок: <code>/gift USER_ID [дней]</code>",
        "admin",
        "tools",
    ),
    HelpEntry(
        "/clear_my_chat",
        "Удалить свою переписку с ботом в личке (алиас <code>/clear_dm</code>, с подтверждением)",
        "admin",
        "tools",
    ),
    HelpEntry(
        "—",
        "Карта автоматических дожимов — <code>docs/FOLLOWUP_MAP.md</code>",
        "admin",
        "notes",
    ),
    HelpEntry(
        "reply в топике поддержки",
        "Ответ на пост с номером тикета → ответ пользователю, тикет закрывается",
        "admin",
        "notes",
    ),
    HelpEntry(
        "reply в топике продаж",
        "Ответ на карточку с User ID → сообщение в личку пользователю",
        "admin",
        "notes",
    ),
    HelpEntry(
        "/admins",
        "Список Telegram ID из таблицы admins",
        "superadmin",
        "access",
    ),
    HelpEntry(
        "/admin_add",
        "Добавить админа: /admin_add USER_ID [note]",
        "superadmin",
        "access",
    ),
    HelpEntry(
        "/admin_del",
        "Удалить админа: /admin_del USER_ID",
        "superadmin",
        "access",
    ),
)

HELP_TITLE_HTML = "<b>📖 Справка по командам</b>"
