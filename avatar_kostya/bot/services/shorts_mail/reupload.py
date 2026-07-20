"""Перезаливка video/voice чужого file_id через токен целевого бота."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Literal, Optional, Tuple

from aiogram import Bot
from aiogram.types import BufferedInputFile

logger = logging.getLogger(__name__)

MediaKind = Literal["video", "voice"]


async def reupload_media_via_bot(
    *,
    source_bot: Bot,
    target_bot: Bot,
    file_id: str,
    kind: MediaKind,
    stash_chat_id: int,
    filename_hint: str = "short",
) -> Tuple[str, Optional[str]]:
    """
    Скачивает файл у source_bot и заливает через target_bot в stash_chat_id.
    Возвращает (новый file_id, media_type).
    Временное сообщение в stash удаляется.
    """
    if stash_chat_id <= 0:
        raise ValueError("stash_chat_id (SUPER_ADMIN_ID) не задан")

    tg_file = await source_bot.get_file(file_id)
    suffix = ".ogg" if kind == "voice" else ".mp4"
    with tempfile.TemporaryDirectory(prefix="shorts_mail_") as tmp:
        local = Path(tmp) / f"{filename_hint}{suffix}"
        await source_bot.download_file(tg_file.file_path, destination=local)
        data = local.read_bytes()
        upload = BufferedInputFile(data, filename=local.name)

        if kind == "voice":
            msg = await target_bot.send_voice(stash_chat_id, voice=upload)
            new_id = msg.voice.file_id if msg.voice else ""
            media_type = "voice"
        else:
            msg = await target_bot.send_video(
                stash_chat_id,
                video=upload,
                supports_streaming=True,
            )
            new_id = msg.video.file_id if msg.video else ""
            media_type = "video"

        try:
            await target_bot.delete_message(stash_chat_id, msg.message_id)
        except Exception as e:
            logger.warning("shorts_mail: не удалось удалить stash-сообщение: %s", e)

    if not new_id:
        raise RuntimeError("target bot не вернул file_id после upload")
    return new_id, media_type
