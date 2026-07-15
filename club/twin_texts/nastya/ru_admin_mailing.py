"""Тексты (RU) для админ-рассылок (`/new_mailing`)."""

from __future__ import annotations

import html
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

# --- Кнопки ---
BTN_YES = "✅ Да"
BTN_NO = "❌ Нет"
BTN_CANCEL = "❌ Отмена"
BTN_PARSE_HTML = "📝 HTML"
BTN_PARSE_MARKDOWN = "📄 Markdown"
BTN_AUD_ALL = "1️⃣ Все активные"
BTN_AUD_HAS_LICENSE = "2️⃣ С лицензией"
BTN_AUD_NO_LICENSE = "3️⃣ Без действующей лицензии"
BTN_AUD_CUSTOM = "✏️ Свой список id"
BTN_MEDIA_DONE = "✅ Готово"
BTN_CONFIRM_CREATE = "✅ Создать кампанию"
BTN_SCHEDULE_NOW = "⚡ Сейчас"
BTN_KIND_CALLBACK = "🔘 Callback"
BTN_KIND_URL = "🔗 URL"
BTN_STYLE_SUCCESS = "🟢 Зелёная"
BTN_STYLE_PRIMARY = "🟡 Жёлтая"
BTN_STYLE_DANGER = "🔴 Красная"
BTN_STYLE_DEFAULT = "⚪ Обычная"

# --- Медиа-пакет ---
MEDIA_BATCH_HELP_HTML = (
    "📎 <b>Пришлите медиафайлы по одному</b> (фото / видео / документ / голос / кружок)."
    "\nПодпись к сообщению не идёт в рассылку — только текст кампании, заданный ранее."
    "\nКомандой <code>/done</code> или кнопкой «Готово» ниже закончите пакет."
)

# --- Сообщения ---
MSG_CANCELLED = "❌ Создание рассылки отменено."
ERR_NO_ACCESS = (
    "⛔ Нет доступа. Telegram ID должен быть в таблице <code>admins</code>."
)
NEW_MAILING_PROMPT_HTML = (
    "📧 <b>Новая рассылка (club)</b>\n\nВведите <b>внутреннее имя</b> кампании:"
)
ERR_NAME_EMPTY_OR_LONG = "❌ Название пустое или длиннее 255 символов."
PROMPT_TEXT_BODY_HTML = "📝 Введите <b>текст сообщения</b> для рассылки:"
ERR_TEXT_EMPTY = "❌ Текст не может быть пустым."
PROMPT_PARSE_MODE_HTML = "📄 Выберите <b>режим форматирования</b>:"
ERR_SCHEDULE_FORMAT = "❌ Дата вида <code>YYYY-MM-DD HH:MM:SS</code>"
PROMPT_REF_LINK_HTML = (
    "🔗 Добавлять к тексту персональный <code>/start ref_&lt;id&gt;</code>?"
)
ERR_MEDIA_TYPE = "❌ Нужно фото, видео, документ, голос / аудио или видеокружок."
ERR_BTN_TEXT_EMPTY = "❌ Текст пуст."
PROMPT_BTN_KIND = "Тип кнопки:"
ERR_VALUE_EMPTY = "❌ Пусто."
PROMPT_AUDIENCE_HTML = "Выберите <b>аудиторию</b>:"
ERR_CUSTOM_IDS_FORMAT = "❌ Нужны целые Telegram id через запятую."
MSG_NO_RECIPIENTS = "📭 Нет получателей в сегменте."
ERR_SAVE_CAMPAIGN = "❌ Не удалось сохранить кампанию (см. лог БД)."
MSG_CALLBACK_CANCELLED = "❌ Отменено."
PROMPT_REF_PER_USER_HTML = "🔗 Рефлинк с id каждому получателю?"
PROMPT_HAS_MEDIA_HTML = "📎 Прикладываем медиа?"
MSG_MEDIA_DONE_BTN_HINT = "Кнопку «Готово» можете нажать тут:"
PROMPT_INLINE_BUTTON_HTML = "🔘 Добавить inline-кнопки к сообщению?"
PROMPT_BTN_MORE_HTML = "Добавить ещё одну кнопку?"
MSG_CHOICE = "Выбор:"
PROMPT_BTN_TEXT_USERS = "Текст кнопки (видят пользователи):"
PROMPT_SEGMENT_HTML = "Сегмент:"
PROMPT_HTTPS_URL_HTML = "Введите <b>HTTPS</b>-ссылку:"
MSG_NOT_SAVED = "❌ Не сохранено."
MSG_CREATING = "⏳ Создаём кампанию…"
PROMPT_BUTTON_STYLE_HTML = "Выберите <b>стиль кнопки</b>:"

CUSTOM_IDS_EXAMPLE = "304631563, 367302291"
CALLBACK_DATA_EXAMPLE = "payment_start"

STYLE_LABELS = {
    "success": "зелёная",
    "primary": "жёлтая",
    "danger": "красная",
    None: "обычная",
    "": "обычная",
    "none": "обычная",
}

MEDIA_TYPE_LABELS = {
    "photo": "фото",
    "video": "видео",
    "document": "документ",
    "voice": "голос",
    "video_note": "кружок",
    "animation": "GIF",
}


def schedule_example_dt() -> str:
    return (datetime.now() + timedelta(minutes=7)).strftime("%Y-%m-%d %H:%M:%S")


def prompt_schedule_html() -> str:
    ex = schedule_example_dt()
    return (
        "⏰ Время старта: <code>YYYY-MM-DD HH:MM:SS</code> или ⚡ ниже:\n"
        f"Пример: <code>{ex}</code>"
    )


def prompt_custom_user_ids_html() -> str:
    return (
        "Список <code>user_id</code> через запятую:\n"
        f"Пример: <code>{CUSTOM_IDS_EXAMPLE}</code>"
    )


def prompt_callback_data_html() -> str:
    return (
        "Введите <code>callback_data</code>:\n"
        f"Пример: <code>{CALLBACK_DATA_EXAMPLE}</code>"
    )


def media_added_html(*, added: int, total: int) -> str:
    return f"+{added}, всего <b>{total}</b> вложени(й)"


def media_ready_prompt_html(*, count: int) -> str:
    return f"✅ Медиа готово: <b>{count}</b> файла(ов). Добавить inline-кнопки?"


def media_ready_short_html(*, count: int) -> str:
    return f"✅ Медиа готово: <b>{count}</b>. Добавить inline-кнопки?"


def prompt_btn_text_nth_html(*, n: int) -> str:
    return f"Текст кнопки №{n} (видят пользователи):"


def button_added_html(*, total: int) -> str:
    return f"✅ Кнопка добавлена, всего <b>{total}</b>.\n{PROMPT_BTN_MORE_HTML}"


def _fmt_when(when: Any) -> str:
    if isinstance(when, datetime):
        return when.strftime("%Y-%m-%d %H:%M:%S")
    return str(when)


def _style_label(style: Any) -> str:
    if not style or style == "none":
        return STYLE_LABELS["none"]
    return STYLE_LABELS.get(str(style), str(style))


def _button_summary(buttons: Optional[List[Dict[str, Any]]]) -> str:
    if not buttons:
        return "нет"
    lines: List[str] = [f"<b>{len(buttons)}</b> шт.:"]
    for i, btn in enumerate(buttons, 1):
        title = html.escape(str(btn.get("text") or ""))
        kind = "callback" if btn.get("callback") else "url" if btn.get("url") else "?"
        val = html.escape(str(btn.get("callback") or btn.get("url") or ""))
        style = _style_label(btn.get("style"))
        lines.append(f"  {i}. «{title}» ({kind}: <code>{val}</code>, стиль: {style})")
    return "\n".join(lines)


def _media_summary(attachments: Optional[List[Dict[str, str]]]) -> str:
    if not attachments:
        return "нет"
    lines = [f"<b>{len(attachments)}</b> шт.:"]
    for i, att in enumerate(attachments, 1):
        t = att.get("type") or "?"
        label = MEDIA_TYPE_LABELS.get(t, t)
        fid = str(att.get("file_id") or "")
        short = f"{fid[:20]}…" if len(fid) > 20 else fid
        lines.append(f"  {i}. {label} (<code>{html.escape(short)}</code>)")
    return "\n".join(lines)


def confirm_blob_html(
    *,
    name: Any,
    text: str,
    when: Any,
    parse_mode: str,
    has_ref_link: bool,
    attachments: Optional[List[Dict[str, str]]],
    buttons: Optional[List[Dict[str, Any]]],
    segment: Any,
    recipient_hint: Any,
    custom_user_ids: Optional[List[int]] = None,
) -> str:
    rf = "да" if has_ref_link else "нет"
    body = text or ""
    text_block = html.escape(body)
    if len(text_block) > 3500:
        text_block = text_block[:3500] + "…"

    lines = [
        "📋 <b>Проверка</b>",
        f"• Имя: <code>{html.escape(str(name))}</code>",
        f"• Текст ({len(body)} симв.):\n<pre>{text_block}</pre>",
        f"• Запуск: <code>{_fmt_when(when)}</code>",
        f"• Parse mode: <code>{html.escape(str(parse_mode))}</code>",
        f"• Рефлинк: {rf}",
        f"• Медиа: {_media_summary(attachments)}",
        f"• Кнопки: {_button_summary(buttons)}",
        f"• Сегмент: <code>{html.escape(str(segment))}</code>",
        f"• Получателей: {recipient_hint}",
    ]
    if segment == "custom" and custom_user_ids:
        ids = ", ".join(str(x) for x in custom_user_ids)
        lines.append(f"• ID списка: <code>{html.escape(ids)}</code>")
    return "\n".join(lines)


def campaign_saved_html(*, campaign_id: int, added: int, total: int) -> str:
    return (
        f"✅ Кампания <code>{campaign_id}</code>, аудитория: добавлено <b>{added}</b> строк "
        f"из <b>{total}</b>."
    )
