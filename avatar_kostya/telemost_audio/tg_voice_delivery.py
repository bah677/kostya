"""TG: полноценное voice (волна + scrub) или MP3-документ."""

from __future__ import annotations

import logging
import subprocess
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from aiogram.types import FSInputFile

from telemost_audio.telegram_voice_opus import (
    TG_VOICE_WAVEFORM_MAX_BYTES,
    ffmpeg_bin,
    max_chunk_sec_for_waveform,
    ogg_path_duration_sec,
    probe_media_duration_sec,
    shrink_ogg_under_limit,
)

logger = logging.getLogger(__name__)


class TgAudioKind(str, Enum):
    VOICE = "voice"
    DOCUMENT = "document"


def is_proper_tg_voice_path(path: Path) -> bool:
    if not path.is_file():
        return False
    duration = ogg_path_duration_sec(path)
    return is_proper_tg_voice(size_bytes=path.stat().st_size, duration_sec=duration)


def is_proper_tg_voice(*, size_bytes: int, duration_sec: Optional[int]) -> bool:
    if duration_sec is None or duration_sec <= 0:
        return False
    return 0 < size_bytes <= TG_VOICE_WAVEFORM_MAX_BYTES


def ensure_under_waveform_limit(path: Path) -> Path:
    if not path.is_file() or path.stat().st_size <= TG_VOICE_WAVEFORM_MAX_BYTES:
        return path
    duration = probe_media_duration_sec(path) or 0.0
    if duration <= 0:
        return path
    shrunk = path.with_name(path.stem + "_1m.ogg")
    if shrink_ogg_under_limit(path, shrunk, duration_sec=duration):
        if shrunk.is_file() and shrunk.stat().st_size < path.stat().st_size:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            shrunk.rename(path)
    return path


def convert_path_to_mp3(src: Path, dst: Path, *, timeout: int = 3600) -> bool:
    if not src.is_file():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin(),
        "-y",
        "-i",
        str(src),
        "-vn",
        "-acodec",
        "libmp3lame",
        "-q:a",
        "4",
        str(dst),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        if proc.returncode != 0:
            logger.error("convert_path_to_mp3: %s", (proc.stderr or "")[-500:])
            return False
        return dst.is_file() and dst.stat().st_size > 500
    except Exception as e:
        logger.exception("convert_path_to_mp3: %s", e)
        return False


def prepare_ogg_path(path: Path) -> tuple[TgAudioKind, Path]:
    """OGG → voice если возможно, иначе MP3-документ рядом с исходником."""
    if not path.is_file():
        return TgAudioKind.DOCUMENT, path

    ensure_under_waveform_limit(path)
    if is_proper_tg_voice_path(path):
        return TgAudioKind.VOICE, path

    mp3 = path.with_suffix(".mp3")
    if convert_path_to_mp3(path, mp3):
        logger.info(
            "ogg %s (%s bytes) → mp3 document",
            path.name,
            path.stat().st_size,
        )
        return TgAudioKind.DOCUMENT, mp3

    logger.warning("prepare_ogg_path: mp3 fallback failed for %s", path.name)
    return TgAudioKind.VOICE, path


def duration_allows_tg_voice(total_sec: float) -> bool:
    """Достаточно ли короткая запись, чтобы уложиться в лимит волны."""
    if total_sec <= 0:
        return False
    return total_sec <= max_chunk_sec_for_waveform(bitrate_kbps=10)


async def send_tg_audio_path(
    bot: Any,
    chat_id: int,
    path: Path,
    *,
    kind: Optional[TgAudioKind] = None,
    filename: Optional[str] = None,
    **kwargs: Any,
) -> Any:
    """Отправить voice или MP3-документ по пути на диске."""
    send_kind = kind
    send_path = path
    if send_kind is None:
        if path.suffix.lower() == ".ogg":
            send_kind, send_path = prepare_ogg_path(path)
        elif path.suffix.lower() == ".mp3":
            send_kind = TgAudioKind.DOCUMENT
        else:
            mp3 = path.with_suffix(".mp3")
            if convert_path_to_mp3(path, mp3):
                send_kind, send_path = TgAudioKind.DOCUMENT, mp3
            else:
                send_kind, send_path = TgAudioKind.VOICE, path

    media = FSInputFile(str(send_path), filename=filename or send_path.name)
    if send_kind == TgAudioKind.VOICE:
        dur = ogg_path_duration_sec(send_path)
        if dur is not None:
            kwargs = dict(kwargs)
            kwargs["duration"] = dur
        return await bot.send_voice(chat_id, media, **kwargs)
    return await bot.send_document(chat_id, media, **kwargs)
