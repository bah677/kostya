"""Тексты (RU) для MediaIdHelperFeature (`/code_id`)."""

from __future__ import annotations

import html

NO_USERNAME = "нет username"

CMD_CODE_ID_HTML = (
    "<b>🖼 Получение file_id</b>\n\n"
    "Отправьте мне любой медиафайл (фото, видео, голосовое, видео-кружок, аудио, документ),\n"
    "а я верну вам его <code>file_id</code>.\n\n"
    "Этот ID можно использовать в рассылках и других местах."
)

ERR_UNSUPPORTED_MEDIA = (
    "❌ Неподдерживаемый тип файла.\n"
    "Отправьте: фото, видео, голосовое, аудио, видео-кружок или документ."
)

ADMIN_CAPTION_TITLE = "<b>📎 Получен file_id</b>\n\n"


def user_media_received_html(
    *,
    media_type: str,
    file_id: str,
    duration: int | None = None,
    file_name: str | None = None,
) -> str:
    response = (
        f"✅ <b>{media_type.upper()} получен!</b>\n\n"
        f"📎 <b>file_id:</b>\n<code>{file_id}</code>\n\n"
        f"📋 <b>Тип:</b> <code>{media_type}</code>"
    )
    if duration:
        response += f"\n⏱ <b>Длительность:</b> {duration} сек"
    if file_name:
        response += f"\n📄 <b>Имя файла:</b> <code>{file_name}</code>"
    return response


def admin_media_caption_html(
    *,
    user_full_name: str,
    username_str: str,
    user_id: int,
    media_type: str,
    file_id: str,
    duration: int | None = None,
    file_name: str | None = None,
) -> str:
    caption = (
        f"{ADMIN_CAPTION_TITLE}"
        f"👤 <b>Пользователь:</b> {html.escape(user_full_name)} "
        f"({html.escape(username_str)})\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
        f"📋 <b>Тип:</b> <code>{html.escape(media_type)}</code>"
    )
    if duration:
        caption += f"\n⏱ <b>Длительность:</b> {duration} сек"
    if file_name:
        caption += f"\n📄 <b>Имя файла:</b> <code>{html.escape(file_name)}</code>"
    caption += f"\n\n📌 <b>file_id:</b>\n<code>{html.escape(file_id)}</code>"
    return caption
