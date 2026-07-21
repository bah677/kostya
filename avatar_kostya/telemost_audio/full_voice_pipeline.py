"""Публикация полной аудио-записи эфира/молитвы голосовым.

Временно: в топик шортсов админ-группы (как клипы), не в клубные топики эфир/молитва.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from aiogram.enums import ParseMode
from aiogram.types import FSInputFile

from bot.utils.rag_admin_context import rag_shorts_chat_topic
from config import config
from telemost_audio.ffmpeg_render import render_full_voice_ogg
from telemost_audio.full_voice_caption import build_full_voice_caption_parts
from telemost_audio.recording_kind import (
    KIND_EFIR,
    KIND_LABELS,
    KIND_MOLITVA,
    KIND_POKAYANIE,
    KIND_QA,
    is_media_recording_kind,
)
from telemost_mail.imap_client import YandexImapClient
from telemost_audio.recording_resolver import wait_and_download_audio

logger = logging.getLogger(__name__)

_active_full: set[str] = set()

# Временно True: полная запись → топик шортсов. Вернуть False → клуб (эфир/молитва/покаяние).
_FULL_VOICE_TO_SHORTS_TOPIC = True


def _target_topic(recording_kind: str) -> tuple[int, Optional[int]]:
    kind = (recording_kind or "").strip().lower()
    if not is_media_recording_kind(kind):
        return 0, None

    if _FULL_VOICE_TO_SHORTS_TOPIC:
        chat, topic = rag_shorts_chat_topic()
        return int(chat or 0), topic

    chat = int(
        getattr(config, "TELEMOST_FULL_VOICE_CHAT_ID", 0)
        or getattr(config, "RAG_GROUP_CHAT_ID", 0)
        or 0
    )
    if kind == KIND_MOLITVA:
        topic = int(getattr(config, "TELEMOST_MOLITVA_TOPIC_ID", 2) or 2)
    elif kind == KIND_POKAYANIE:
        topic = int(getattr(config, "TELEMOST_POKAYANIE_TOPIC_ID", 0) or 0)
        if not topic:
            logger.warning(
                "telemost_full_voice: TELEMOST_POKAYANIE_TOPIC_ID не задан — пропуск клубной выкладки"
            )
            return 0, None
    elif kind == KIND_QA:
        topic = int(getattr(config, "TELEMOST_QA_TOPIC_ID", 0) or 0)
        if not topic:
            logger.warning(
                "telemost_full_voice: TELEMOST_QA_TOPIC_ID не задан — пропуск клубной выкладки"
            )
            return 0, None
    else:
        topic = int(getattr(config, "TELEMOST_EFIR_TOPIC_ID", 3) or 3)
    return chat, topic or None


def enqueue_telemost_full_voice(
    bot_app: Any,
    pending_id: uuid.UUID,
    row: Dict[str, Any],
    meta: Dict[str, Any],
    *,
    recording_kind: str,
    force: bool = False,
) -> bool:
    if not getattr(config, "TELEMOST_FULL_VOICE_ENABLED", True):
        return False
    kind = (recording_kind or "").strip().lower()
    if not is_media_recording_kind(kind):
        return False
    pid = str(pending_id)
    if pid in _active_full and not force:
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


async def enqueue_telemost_full_voice_by_meeting_id(
    bot_app: Any,
    meeting_id: str,
    *,
    force: bool = True,
    recording_kind: Optional[str] = None,
) -> tuple[bool, str]:
    """Выложить полную голосовую запись встречи по номеру."""
    from html import escape as html_escape

    from telemost_audio.recording_kind import recording_kind_from_content_type

    storage = getattr(bot_app, "user_storage", None)
    if storage is None:
        return False, "Хранилище недоступно"
    mid = (meeting_id or "").strip()
    if not mid:
        return False, "Укажите номер встречи"
    row = await storage.get_telemost_pending_by_meeting_id(mid)
    if not row:
        return False, f"Конспект встречи №<code>{html_escape(mid)}</code> не найден"
    status = str(row.get("status") or "")
    if status != "indexed":
        return False, (
            f"№<code>{html_escape(mid)}</code> ещё не в RAG "
            f"(status=<code>{html_escape(status)}</code>). Сначала загрузите конспект."
        )
    try:
        pending_id = uuid.UUID(str(row["id"]))
    except (ValueError, TypeError):
        return False, "Некорректный pending_id"

    # meta как в audio pipeline
    from telemost_mail.classifier_llm import TelemostClassification

    clf_raw = row.get("classification") or {}
    classification = TelemostClassification(
        is_club_meeting=bool(clf_raw.get("is_club_meeting")),
        recommend_index=bool(clf_raw.get("recommend_index")),
        title=str(clf_raw.get("title") or ""),
        meeting_topic=str(clf_raw.get("meeting_topic") or ""),
        content_type=str(clf_raw.get("content_type") or ""),
        content_category=str(clf_raw.get("content_category") or "dialog"),
        product=str(clf_raw.get("product") or ""),
        tags=str(clf_raw.get("tags") or ""),
        summary=str(clf_raw.get("summary") or ""),
        admin_note=str(clf_raw.get("admin_note") or ""),
        reason=str(clf_raw.get("reason") or ""),
    )
    source_label = classification.title or (row.get("subject") or "Телемост")[:80]
    meta = classification.as_chroma_metadata(source_label=source_label)
    extra = row.get("extra_metadata") or {}
    if isinstance(extra, dict) and extra.get("meeting_date"):
        meta["date"] = str(extra["meeting_date"])[:32]

    kind = (recording_kind or "").strip().lower()
    if not is_media_recording_kind(kind):
        kind = recording_kind_from_content_type(str(clf_raw.get("content_type") or ""))

    title = meta.get("topic_title") or meta.get("source") or row.get("subject") or "Запись"
    if not enqueue_telemost_full_voice(
        bot_app,
        pending_id,
        row,
        meta,
        recording_kind=kind,
        force=force,
    ):
        return False, "Полная запись уже выкладывается"
    return True, f"№{mid} ({KIND_LABELS.get(kind, kind)}): {str(title)[:90]}"


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

    kind_label = KIND_LABELS.get(recording_kind, "Запись")

    try:
        if not bot or not chat_id or not storage:
            logger.warning(
                "telemost_full_voice: missing bot/chat/storage chat=%s",
                chat_id,
            )
            return

        extra = row.get("extra_metadata") or {}
        meeting_id = (extra.get("meeting_id") if isinstance(extra, dict) else "") or ""
        if not meeting_id:
            clf = row.get("classification") or {}
            if isinstance(clf, dict):
                ex = clf.get("extra") or {}
                if isinstance(ex, dict):
                    meeting_id = str(ex.get("meeting_id") or "")

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
        title_plain, desc_plain, caption = await build_full_voice_caption_parts(
            meeting_title=str(title),
            summary=summary,
            transcript_excerpt=transcript[:12000],
            recording_kind=recording_kind,
            philosophy_hint=philosophy,
        )
        if _FULL_VOICE_TO_SHORTS_TOPIC:
            prefix = f"📻 Полная запись · {kind_label}\n\n"
            if caption and not caption.startswith("📻"):
                caption = prefix + caption
            elif not caption:
                caption = prefix.strip()
            if len(caption) > 1024:
                caption = caption[:1021].rstrip() + "…"

        sent = await bot.send_voice(
            chat_id,
            FSInputFile(str(voice_path)),
            caption=caption,
            parse_mode=ParseMode.HTML,
            message_thread_id=topic_id,
        )
        try:
            await storage.create_caption_edit_session(
                entity_type="full_voice",
                chat_id=int(chat_id),
                root_message_id=int(sent.message_id),
                caption_html=caption,
                title=title_plain,
                description=desc_plain,
                media_kind="voice",
                topic_id=int(topic_id or 0),
                pending_id=pending_id,
                meeting_id=str(meeting_id or ""),
                context={
                    "meeting_title": str(title),
                    "recording_kind": recording_kind,
                    "kind_label": kind_label,
                    "transcript_excerpt": transcript[:8000],
                    "summary": summary[:1500],
                },
            )
        except Exception as e:
            logger.warning("full_voice caption session: %s", e)
        logger.info(
            "telemost_full_voice sent kind=%s chat=%s topic=%s pending=%s dest=%s",
            recording_kind,
            chat_id,
            topic_id,
            pid,
            "shorts_topic" if _FULL_VOICE_TO_SHORTS_TOPIC else "club_topic",
        )

    except Exception as e:
        logger.exception("telemost_full_voice pending=%s: %s", pid, e)
    finally:
        _active_full.discard(pid)
