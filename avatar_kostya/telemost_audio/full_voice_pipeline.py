"""Публикация полной аудио-записи эфира/молитвы голосовым в RAG-группу."""

from __future__ import annotations

import asyncio
import logging
import uuid
from html import escape as html_escape
from pathlib import Path
from typing import Any, Dict, Optional

from aiogram.enums import ParseMode
from aiogram.types import FSInputFile

from config import config
from telemost_audio.ffmpeg_render import render_full_voice_ogg
from telemost_audio.full_voice_caption import build_full_voice_caption
from telemost_audio.recording_kind import KIND_EFIR, KIND_MOLITVA
from telemost_mail.imap_client import YandexImapClient
from telemost_audio.recording_resolver import wait_and_download_audio

logger = logging.getLogger(__name__)

_active_full: set[str] = set()


def _target_topic(recording_kind: str) -> tuple[int, Optional[int]]:
    chat = int(
        getattr(config, "TELEMOST_FULL_VOICE_CHAT_ID", 0)
        or getattr(config, "RAG_GROUP_CHAT_ID", 0)
        or 0
    )
    kind = (recording_kind or "").strip().lower()
    if kind == KIND_MOLITVA:
        topic = int(getattr(config, "TELEMOST_MOLITVA_TOPIC_ID", 2) or 2)
    elif kind == KIND_EFIR:
        topic = int(getattr(config, "TELEMOST_EFIR_TOPIC_ID", 3) or 3)
    else:
        return 0, None
    return chat, topic or None


def enqueue_telemost_full_voice(
    bot_app: Any,
    pending_id: uuid.UUID,
    row: Dict[str, Any],
    meta: Dict[str, Any],
    *,
    recording_kind: str,
) -> bool:
    if not getattr(config, "TELEMOST_FULL_VOICE_ENABLED", True):
        return False
    kind = (recording_kind or "").strip().lower()
    if kind not in {KIND_EFIR, KIND_MOLITVA}:
        return False
    pid = str(pending_id)
    if pid in _active_full:
        logger.info("telemost_full_voice: already running pending_id=%s", pid)
        return False
    _active_full.add(pid)
    asyncio.create_task(
        _run_full_voice_pipeline(
            bot_app,
            pending_id,
            row,
            meta,
            recording_kind=kind,
        ),
        name=f"telemost_full_voice_{pid[:8]}",
    )
    return True


async def _run_full_voice_pipeline(
    bot_app: Any,
    pending_id: uuid.UUID,
    row: Dict[str, Any],
    meta: Dict[str, Any],
    *,
    recording_kind: str,
) -> None:
    pid = str(pending_id)
    bot = getattr(bot_app, "bot", None)
    storage = getattr(bot_app, "user_storage", None)
    chat_id, topic_id = _target_topic(recording_kind)
    title = (
        meta.get("topic_title")
        or meta.get("source")
        or row.get("subject")
        or "Запись"
    )
    summary = (meta.get("meeting_topic") or meta.get("summary") or "").strip()
    if not summary:
        clf = row.get("classification") or {}
        if isinstance(clf, dict):
            summary = str(clf.get("summary") or "")

    kind_label = "Молитва" if recording_kind == KIND_MOLITVA else "Эфир"

    try:
        if not bot or not chat_id or not storage:
            logger.warning("telemost_full_voice: missing bot/chat/storage")
            return

        extra = row.get("extra_metadata") or {}
        meeting_id = (extra.get("meeting_id") if isinstance(extra, dict) else "") or ""
        transcript = (row.get("transcript_text") or "").strip()

        imap = YandexImapClient(
            getattr(config, "TELEMOST_MAIL_LOGIN", "") or "",
            getattr(config, "TELEMOST_MAIL_PASSWORD", "") or "",
            host=getattr(config, "TELEMOST_MAIL_IMAP_HOST", "imap.yandex.ru"),
            port=int(getattr(config, "TELEMOST_MAIL_IMAP_PORT", 993) or 993),
            folder=getattr(config, "TELEMOST_MAIL_FOLDER", "INBOX") or "INBOX",
        )

        audio_path = await wait_and_download_audio(
            meeting_id,
            storage=storage,
            imap=imap,
            notify=None,
        )
        if not audio_path:
            logger.warning(
                "telemost_full_voice: no audio meeting_id=%s pending=%s",
                meeting_id,
                pid,
            )
            return

        work_root = Path(
            getattr(config, "TELEMOST_AUDIO_WORK_DIR", "data/telemost_audio_clips")
        )
        work_dir = work_root / f"full_{pid[:8]}"
        voice_path = await render_full_voice_ogg(
            audio_path,
            work_dir=work_dir,
            stem=f"{recording_kind}_{meeting_id or pid[:8]}",
        )
        if not voice_path:
            logger.error("telemost_full_voice: ffmpeg failed pending=%s", pid)
            return

        philosophy = getattr(config, "TELEMOST_SHORTS_PHILOSOPHY_HINT", "") or ""
        caption = await build_full_voice_caption(
            meeting_title=str(title),
            summary=summary,
            transcript_excerpt=transcript[:3000],
            recording_kind=recording_kind,
            philosophy_hint=philosophy,
        )

        await bot.send_voice(
            chat_id,
            FSInputFile(str(voice_path)),
            caption=caption,
            parse_mode=ParseMode.HTML,
            message_thread_id=topic_id,
        )
        logger.info(
            "telemost_full_voice sent kind=%s chat=%s topic=%s pending=%s",
            recording_kind,
            chat_id,
            topic_id,
            pid,
        )

        shorts_chat, shorts_topic = (
            int(config.rag_shorts_chat_id or 0),
            int(getattr(config, "RAG_SHORTS_TOPIC_ID", 0) or 0) or None,
        )
        if shorts_chat:
            try:
                await bot.send_message(
                    shorts_chat,
                    f"📻 Полная запись <b>{html_escape(kind_label)}</b> опубликована "
                    f"в топике клуба для «{html_escape(str(title)[:100])}».",
                    parse_mode=ParseMode.HTML,
                    message_thread_id=shorts_topic,
                )
            except Exception as e:
                logger.warning("telemost_full_voice notify shorts: %s", e)

    except Exception as e:
        logger.exception("telemost_full_voice pending=%s: %s", pid, e)
    finally:
        _active_full.discard(pid)
