"""Reply-редактура caption: текст или голос на сообщение full voice / шортса."""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID

from aiogram import Dispatcher, F
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import StateFilter
from aiogram.types import Message

from bot.features.base import BaseFeature
from bot.utils.rag_admin_context import is_rag_shorts_message
from config import config
from telemost_audio.caption_revision import (
    format_audio_caption_html,
    format_title_description_html,
    revise_caption_with_feedback,
)
from telemost_audio.recording_kind import ensure_kind_title_prefix

logger = logging.getLogger(__name__)

_VOICE_PREFIX_RE = re.compile(
    r"^\[(?:голосовое|видео содержит речь|кружочек с речью):\s*",
    re.IGNORECASE,
)


def _clean_media_text(text: str) -> str:
    t = (text or "").strip()
    t = _VOICE_PREFIX_RE.sub("", t)
    if t.endswith("]"):
        t = t[:-1].strip()
    return t.strip()


class CaptionEditorFeature(BaseFeature):
    name = "caption_editor"

    def __init__(self) -> None:
        super().__init__()
        self._app: Any = None

    def set_bot(self, app: Any) -> None:
        self._app = app

    async def _is_admin(self, user_id: int) -> bool:
        if config.SUPER_ADMIN_ID and user_id == config.SUPER_ADMIN_ID:
            return True
        if self._app and await self._app.user_storage.is_bot_admin(user_id):
            return True
        return False

    def register_handlers(self, dispatcher: Dispatcher) -> None:
        dispatcher.message.register(
            self.on_reply_feedback,
            F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
            F.reply_to_message,
            StateFilter(None),
        )

    async def on_reply_feedback(self, message: Message) -> None:
        if message.from_user is None or message.reply_to_message is None:
            raise SkipHandler
        if not is_rag_shorts_message(message.chat.id, message.message_thread_id):
            raise SkipHandler
        # Не перехватываем #club / #biblia (shorts mail wizard)
        text_raw = (message.text or "").strip()
        if re.match(r"(?i)^\s*#(club|biblia)\s*$", text_raw):
            raise SkipHandler
        if not await self._is_admin(message.from_user.id):
            raise SkipHandler

        storage = self._app.user_storage if self._app else None
        if storage is None:
            raise SkipHandler

        reply = message.reply_to_message
        session = await storage.get_caption_edit_session_by_any_message(
            message.chat.id, reply.message_id
        )
        if not session:
            raise SkipHandler

        feedback = await self._extract_feedback_text(message)
        if not feedback:
            await message.reply(
                "Напишите замечание текстом или голосом — "
                "доработаю title/description с учётом контекста предыдущих правок."
            )
            return

        await message.reply("✏️ Учитываю замечание, переписываю подпись…")

        entity_type = str(session.get("entity_type") or "")
        ctx = session.get("context_json") or {}
        iters = session.get("iterations_json") or []
        title = str(session.get("title") or "")
        description = str(session.get("description") or "")

        revised = await revise_caption_with_feedback(
            entity_type=entity_type,
            current_title=title,
            current_description=description,
            feedback=feedback,
            context=ctx if isinstance(ctx, dict) else {},
            iterations=iters if isinstance(iters, list) else [],
        )
        if not revised:
            await message.reply("Не удалось доработать подпись. Попробуйте ещё раз.")
            return

        new_title = revised.title
        new_desc = revised.description
        kind = str((ctx or {}).get("recording_kind") or "")
        if entity_type == "full_voice" and kind:
            new_title = ensure_kind_title_prefix(new_title, kind)

        if entity_type == "audio_short":
            new_html = format_audio_caption_html(
                headline=new_title,
                summary=new_desc,
                bible_quote=str((ctx or {}).get("bible_quote") or ""),
                bible_ref=str((ctx or {}).get("bible_ref") or ""),
            )
        elif entity_type == "video_short":
            idx = (ctx or {}).get("clip_index") or "?"
            reason = str((ctx or {}).get("moment_reason") or new_desc)[:200]
            from html import escape as html_escape

            new_html = (
                f"<b>Short {html_escape(str(idx))}</b> · {html_escape(new_title)}\n"
                f"{html_escape(new_desc)}\n"
                f"<i>{html_escape(reason)}</i>"
            )
            if len(new_html) > 1024:
                new_html = new_html[:1021].rstrip() + "…"
        else:
            new_html = format_title_description_html(new_title, new_desc)
            if str(session.get("caption_html") or "").startswith("📻"):
                # сохраняем префикс полной записи в топике шортсов
                prefix_line = str(session.get("caption_html") or "").split("\n", 1)[0]
                if prefix_line.startswith("📻"):
                    new_html = f"{prefix_line}\n\n{new_html}"
                    if len(new_html) > 1024:
                        new_html = new_html[:1021].rstrip() + "…"

        edited = False
        try:
            await self._app.bot.edit_message_caption(
                chat_id=message.chat.id,
                message_id=int(session.get("current_message_id") or reply.message_id),
                caption=new_html,
                parse_mode=ParseMode.HTML,
            )
            edited = True
        except Exception as e:
            logger.info("caption edit failed, will send text preview: %s", e)

        sid = session.get("id")
        try:
            sid_uuid = sid if isinstance(sid, UUID) else UUID(str(sid))
        except Exception:
            sid_uuid = None

        if sid_uuid:
            await storage.append_caption_edit_iteration(
                sid_uuid,
                role="admin",
                content=feedback,
            )
            await storage.append_caption_edit_iteration(
                sid_uuid,
                role="assistant",
                content=revised.note or f"title={new_title}\n{new_desc}",
                title=new_title,
                description=new_desc,
                caption_html=new_html,
                current_message_id=int(
                    session.get("current_message_id") or reply.message_id
                ),
            )

        note = (revised.note or "").strip()
        head = "✅ Подпись обновлена." if edited else "✅ Новая версия подписи:"
        body = f"{head}\n\n{new_html}"
        if note:
            body += f"\n\n<i>{note}</i>"
        if not edited:
            body += (
                "\n\n<i>Telegram не дал отредактировать caption у этого сообщения — "
                "ниже текст для копирования/ручной правки.</i>"
            )
        await message.reply(body, parse_mode=ParseMode.HTML)

    async def _extract_feedback_text(self, message: Message) -> str:
        if message.text and message.text.strip():
            return message.text.strip()
        if message.caption and message.caption.strip() and not (
            message.voice or message.audio or message.video or message.video_note
        ):
            return message.caption.strip()

        # голос / аудио / видео — через медиапроцессор
        app = self._app
        if not app or not getattr(app, "media_processor", None):
            return (message.caption or "").strip()

        try:
            processed = await app.media_processor.process_message(
                message,
                message.from_user.id if message.from_user else 0,
            )
            text = _clean_media_text(processed.text or "")
            if text and text not in (
                "[ошибка обработки]",
                "[не удалось скачать файл]",
                "[файл слишком большой для обработки]",
            ):
                return text
        except Exception as e:
            logger.warning("caption editor media process: %s", e)

        # fallback: прямое whisper для voice/audio
        if message.voice or message.audio:
            return await self._transcribe_simple(message)
        return (message.caption or "").strip()

    async def _transcribe_simple(self, message: Message) -> str:
        app = self._app
        if not app or not getattr(app, "openai_client", None):
            return ""
        file_id = None
        duration = None
        if message.voice:
            file_id = message.voice.file_id
            duration = message.voice.duration
        elif message.audio:
            file_id = message.audio.file_id
            duration = message.audio.duration
        if not file_id:
            return ""
        try:
            from bot.media_processing.downloader import FileDownloader

            dl = FileDownloader()
            path = await dl.download_file(file_id, app.bot)
            if not path:
                return ""
            try:
                text = await app.openai_client.transcribe_voice(
                    path,
                    message.from_user.id if message.from_user else 0,
                    duration_sec=duration,
                )
                return (text or "").strip()
            finally:
                await dl.cleanup_file(path)
        except Exception as e:
            logger.warning("caption editor whisper: %s", e)
            return ""
