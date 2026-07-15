"""Тексты (RU) для AdminConsoleFeature."""

from __future__ import annotations

import html as html_mod
from typing import Any

# Разделитель частей HTML-отчёта (виден в Telegram).
REPORT_HTML_PART_SEP = "\n\n━━━━━━━━━━━━━━━━━━━━━\n\n"
ANSWER_BLOCK_SEP = "\n\n━━━━━━━━━━━━━━━━━━━━━\n"

# --- Доступ ---
ERR_NO_CONSOLE_ACCESS = (
    "⛔ Нет доступа. Нужна строка в <code>admins</code> или "
    "<code>SUPER_ADMIN_ID</code> в .env с вашим Telegram ID."
)
ERR_SUPER_ADMIN_NOT_SET = "⛔ SUPER_ADMIN_ID не задан в .env."
ERR_SUPER_ADMIN_ONLY = "⛔ Доступно только супер-админу (SUPER_ADMIN_ID)."

# --- /admin ---
ADMIN_CONSOLE_HELP_HTML = (
    "<b>Админ-консоль (club)</b>\n\n"
    "Полный список с учётом уровня доступа: <code>/help</code>\n\n"
    "{body}"
)


def admin_console_help_html(*, body: str) -> str:
    return ADMIN_CONSOLE_HELP_HTML.format(body=body)


# --- Управление admins ---
ADMINS_LIST_EMPTY = "Список admins пуст."
ADMINS_LIST_HEADER = "<b>Admins:</b>"

ADMIN_ADD_USAGE = "Использование: <code>/admin_add 123456789 [note]</code>"
ADMIN_DEL_USAGE = "Использование: <code>/admin_del 123456789</code>"
ERR_USER_ID_NOT_NUMBER = "❌ user_id должен быть числом."


def admin_added_html(*, uid: int) -> str:
    return f"✅ Добавлен admin: <code>{uid}</code>"


def admin_removed_html(*, uid: int) -> str:
    return f"✅ Удалён admin: <code>{uid}</code>"


ERR_ADMIN_ADD_FAILED = "❌ Не удалось добавить admin (см. логи)."
ERR_ADMIN_DEL_FAILED = "❌ Не удалось удалить admin (см. логи)."


# --- /report ---
ERR_REPORT_BUILD = "Ошибка сборки отчёта: {exc}"
ERR_REPORT_NOTHING_TO_SEND = (
    "Нечего отправить: отключены и v2, и legacy. "
    "Проверьте аргументы или <code>REPORT_LEGACY_ENABLED</code>."
)
REPORT_MODE_DEFAULT = "отчёт"
REPORT_MODE_V2_NO_LLM_SUFFIX = ", без DeepSeek"


def report_header_html(*, mode_note: str) -> str:
    return (
        f"📊 <b>Клубный отчёт</b>\n"
        f"<i>Сформирован по /report ({html_mod.escape(mode_note)})</i>\n\n"
    )


# --- Воронки и БД ---
ERR_DB_UNAVAILABLE = "❌ База данных недоступна."


def mailing_funnel_bad_id_html(*, part: str) -> str:
    return (
        f"❌ Не число: <code>{html_mod.escape(part)}</code>. "
        "Пример: <code>/mailing_funnel 12 15 20</code>"
    )


ERR_FOLLOWUP_LEADS_FAILED = (
    "❌ Не удалось собрать отчёт. Подробности в логах бота."
)
ERR_REF_FUNNEL_NOT_FOUND = (
    "❌ Не найдено ref_key по аргументам. "
    "Без аргументов — <code>/ref_funnel</code> (каталог)."
)

# --- /gift ---
GIFT_USAGE_HTML = (
    "🎁 <b>Лицензия в подарок</b>\n\n"
    "Формат: <code>/gift USER_ID</code> — затем число дней\n"
    "или <code>/gift USER_ID ДНЕЙ</code> сразу.\n\n"
    "Клуб: «Любящие Бога»."
)
ERR_GIFT_USER_ID_NOT_NUMBER = "❌ USER_ID должен быть числом (Telegram ID)."
ERR_GIFT_USER_ID_NOT_POSITIVE = "❌ USER_ID должен быть положительным."
ERR_GIFT_DAYS_NOT_INTEGER = "❌ Дней должно быть целое число."


def gift_ask_days_html(*, target_uid: int) -> str:
    return (
        f"🎁 Выдать доступ пользователю <code>{target_uid}</code>.\n\n"
        "На сколько дней? (число от 1 до 3650)"
    )


GIFT_ENTER_DAYS = "Введите число дней (например, 30)."
GIFT_SESSION_RESET = "❌ Сессия сброшена. Начните снова: /gift USER_ID"

# --- /clear_my_chat ---
ERR_CLEAR_DM_ONLY = "Команда работает только в личке с ботом."
BTN_CLEAR_YES = "✅ Да, удалить всё"
BTN_CLEAR_NO = "❌ Отмена"


def clear_chat_confirm_html(*, message_count: int) -> str:
    return (
        "<b>⚠️ Удаление истории в личке</b>\n\n"
        f"Будет очищена <b>ваша</b> переписка с ботом в базе "
        f"(сейчас сообщений: <b>{message_count}</b>).\n\n"
        "Контекст ИИ-агента начнётся с нуля. Заказы, лицензии и админ-права "
        "не затрагиваются.\n\n"
        "<i>Подтвердите кнопкой ниже.</i>"
    )


CB_ERR_INVALID_DATA = "Некорректные данные"
CB_ERR_GENERIC = "Ошибка"
CB_ERR_WRONG_USER = "Это подтверждение не для вас"
CB_ERR_NO_ACCESS = "Нет доступа"
CLEAR_CANCELLED_HTML = "❌ Удаление истории отменено."


def clear_chat_done_html(*, stats: dict[str, Any]) -> str:
    return (
        "<b>✅ История личного чата очищена</b>\n\n"
        f"• messages (soft delete): {stats.get('messages', 0)}\n"
        f"• conversation_history: {stats.get('conversation_history', 0)}\n"
        "• сессия агента сброшена"
    )


CB_CLEAR_DONE = "Готово"

# --- /churn ---
CHURN_BUSY_HTML = (
    "⏳ Собираю расширенную статистику по отвалу. "
    "Затем запрос к DeepSeek — может занять 1–3 минуты."
)
CHURN_REPORT_HEADER = "<b>📉 Отчёт по отвалу</b>\n<i>Команда /churn</i>\n\n"
ERR_CHURN_BUILD = "Ошибка сборки отчёта по отвалу: {exc}"
DEEPSEEK_CONCLUSION_HEADER = (
    "<b>🤖 Заключение DeepSeek (по данным отчёта и aboutclub)</b>\n\n"
)
DEEPSEEK_CONCLUSION_FAILED = (
    "<b>🤖 Заключение DeepSeek</b>\n\n"
    "Не удалось получить ответ API. Проверьте ключ "
    "<code>DEEPSEEK_API_KEY</code> и лимиты."
)
DEEPSEEK_CONCLUSION_NO_KEY = (
    "<b>🤖 Заключение DeepSeek</b>\n\n"
    "Ключ <code>DEEPSEEK_API_KEY</code> не задан — выведен только цифровой отчёт."
)

# --- /graf ---
GRAF_PICK_METRIC = "📈 Выберите метрику:"
BTN_GRAF_TOTAL_AMOUNT_DAY = "💰 Сумма оплат (день)"
BTN_GRAF_PAID_ORDERS = "✅ Оплаченные заказы"
BTN_GRAF_PENDING_ORDERS = "🕒 Неоплаченные заказы"
BTN_GRAF_ACTIVE_USERS = "👥 Активные за вчера"
BTN_GRAF_NEW_USERS = "🆕 Новые за вчера"
BTN_GRAF_TOTAL_USERS = "🧾 Всего пользователей"
BTN_GRAF_MONTH_TOTAL_AMOUNT = "💳 Сумма оплат (месяц)"
BTN_GRAF_MONTH_PAID_ORDERS = "📅 Оплаты (месяц)"
BTN_GRAF_PERIOD_7 = "7 дней"
BTN_GRAF_PERIOD_30 = "30 дней"
BTN_GRAF_PERIOD_90 = "90 дней"
BTN_GRAF_PERIOD_180 = "180 дней"
BTN_GRAF_PERIOD_365 = "365 дней"
GRAF_ERR_PICK_METRIC_FIRST = "Сначала выберите метрику"
GRAF_ERR_BAD_PERIOD = "Некорректный период"
GRAF_BUILDING = "Строю график…"

GRAF_METRIC_TITLES: dict[str, tuple[str, str]] = {
    "total_users": ("Всего пользователей", "#2E86AB"),
    "active_users": ("Активные за вчера", "#E67E22"),
    "new_users": ("Новые за вчера", "#9B59B6"),
    "pending_orders": ("Неоплаченные за вчера", "#C0392B"),
    "paid_orders": ("Оплаченные за вчера", "#27AE60"),
    "total_amount": ("Сумма оплат за вчера (₽)", "#16A085"),
    "month_paid_orders": ("Оплаченные за месяц", "#2980B9"),
    "month_total_amount": ("Сумма оплат за месяц (₽)", "#8E44AD"),
    "active_licenses": ("Активные лицензии", "#D35400"),
    "users_expired": ("Просроченные лицензии", "#7F8C8D"),
}


def graf_pick_period_html(*, metric: str) -> str:
    return f"📈 Метрика: <b>{html_mod.escape(metric)}</b>\nВыберите период:"


def graf_unknown_metric_html(*, metric: str, allowed: str) -> str:
    return (
        f"❌ Неизвестная метрика <code>{html_mod.escape(metric)}</code>.\n"
        f"Доступно: <code>{html_mod.escape(allowed)}</code>"
    )


GRAF_NOT_ENOUGH_POINTS = (
    "ℹ️ Недостаточно точек для графика (нужно минимум 2 снепшота)."
)
ERR_GRAF_MATPLOTLIB = (
    "Для /graf нужен matplotlib. Установите зависимость и перезапустите бота."
)


def graf_caption_html(*, title: str, days: int, point_count: int) -> str:
    return (
        f"📈 <b>{html_mod.escape(title)}</b>\n"
        f"Период: последние {days} дн., точек: {point_count}"
    )


ERR_GRAF_SEND_FAILED = "Не удалось отправить график: {exc}"
GRAF_SENT_TO_DM = "✅ График отправлен вам в личку."

CHART_XLABEL = "Дата"
CHART_YLABEL = "Значение"


def chart_change_annotation(*, diff: float, pct: float) -> str:
    return f"Изменение: {diff:+,.2f} ({pct:+.1f}%)"

# --- Топики поддержки / продаж / форум ---
SUPPORT_PROCESSING = "⏳ Записываю ответ в тикет…"
ERR_TICKET_NOT_FOUND_IN_MSG = (
    "Не найден номер тикета (ожидается TKT_CL… / TKT_BB…) в сообщении."
)
ERR_EMPTY_REPLY = "Пустой ответ."


def err_ticket_closed_or_missing(*, ticket_number: str) -> str:
    return f"Тикет {ticket_number} не найден или уже закрыт."


SUPPORT_REPLY_SENT_OK = (
    "✅ Ответ по тикету отправлен пользователю; тикет закрыт."
)
SUPPORT_REPLY_SEND_FAILED = (
    "⚠️ Ответ записан, но отправка пользователю не удалась — "
    "<b>доставка включит фоновый цикл поддержки.</b>"
)
SUPPORT_ANSWER_BLOCK_HEADER = "✅ <b>Ответ поддержки</b>\n"
SUPPORT_ANSWER_BLOCK_ADMIN = "👤 <b>Админ:</b> "
SUPPORT_ANSWER_BLOCK_TIME = "⏰ <b>Время:</b> "

THREAD_PROCESSING = "⏳ Записываю ответ…"
ERR_USER_ID_NOT_IN_MSG = "Не удалось извлечь User ID из сообщения."
CLUB_MESSAGE_TO_USER_HTML = "💬 <b>Сообщение от отдела клуба</b>\n\n"


def err_dm_send_failed(*, exc: Exception) -> str:
    return f"Не удалось отправить в личку: {exc}"[:200]


def sent_to_user_html(*, target_uid: int) -> str:
    return f"✅ Отправлено пользователю <code>{target_uid}</code>."


SALES_ANSWER_BLOCK_HEADER = "✅ <b>Ответ отдела продаж</b>\n"
SALES_ANSWER_BLOCK_MANAGER = "👤 <b>Менеджер:</b> "
SALES_ANSWER_BLOCK_TIME = "⏰ <b>Время:</b> "
HASHTAG_CLOSED = "#закрыт"

ERR_FORUM_USER_UNKNOWN = (
    "Не удалось определить пользователя: "
    "топик не найден в маппинге и User ID отсутствует в исходном сообщении."
)


def forum_sent_confirm_html(
    *,
    target_uid: int,
    admin_name: str,
    admin_username_suffix: str,
    ts: str,
) -> str:
    return (
        f"✅ <b>Отправлено</b> пользователю <code>{target_uid}</code>\n"
        f"👤 {html_mod.escape(admin_name)}{html_mod.escape(admin_username_suffix)} • {ts}"
    )


# --- Личные уведомления админу ---
DM_ERR_HEADER = "⛔ <b>Ошибка</b>\n\n"
