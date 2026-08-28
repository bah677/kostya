"""TG: полноценное voice (волна + scrub) или MP3-документ."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, Message

from bot.services.prayer_tts_style import (
    TG_VOICE_WAVEFORM_MAX_BYTES,
    ogg_opus_duration_sec,
    shrink_ogg_bytes,
)

logger = logging.getLogger(__name__)


class TgAudioKind(str, Enum):
    VOICE = "voice"
    DOCUMENT = "document"


@dataclass(frozen=True)
class TgAudioPayload:
    kind: TgAudioKind
    data: bytes
    filename: str
    duration_sec: Optional[int] = None


def is_proper_tg_voice(*, size_bytes: int, duration_sec: Optional[int]) -> bool:
    """Voice с волной и перемоткой: OGG ≤1 МиБ и известная длительность."""
    if duration_sec is None or duration_sec <= 0:
        return False
    return 0 < size_bytes <= TG_VOICE_WAVEFORM_MAX_BYTES


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"



def bytes_to_mp3(audio: bytes, *, suffix: str = ".ogg") -> Optional[bytes]:
    if not audio:
        return None
    with tempfile.TemporaryDirectory(prefix="mp3_out_") as tmp:
        root = Path(tmp)
        src = root / f"in{suffix}"
        dst = root / "out.mp3"
        src.write_bytes(audio)
        cmd = [
            _ffmpeg_bin(),
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
                cmd, capture_output=True, text=True, timeout=3600, check=False
            )
            if proc.returncode != 0:
                logger.error("bytes_to_mp3: %s", (proc.stderr or "")[-500:])
                return None
            if not dst.is_file() or dst.stat().st_size < 200:
                return None
            return dst.read_bytes()
        except Exception as e:
            logger.exception("bytes_to_mp3: %s", e)
            return None


def prepare_ogg_bytes(ogg: bytes, *, filename_base: str = "audio") -> Optional[TgAudioPayload]:
    """OGG → voice если укладывается в лимит волны, иначе MP3-документ."""
    if not ogg:
        return None

    duration = ogg_opus_duration_sec(ogg)
    if is_proper_tg_voice(size_bytes=len(ogg), duration_sec=duration):
        return TgAudioPayload(
            TgAudioKind.VOICE,
            ogg,
            f"{filename_base}.ogg",
            duration,
        )

    shrunk = shrink_ogg_bytes(ogg)
    if shrunk:
        duration = ogg_opus_duration_sec(shrunk)
        if is_proper_tg_voice(size_bytes=len(shrunk), duration_sec=duration):
            logger.info(
                "ogg shrunk %s→%s bytes — voice with waveform",
                len(ogg),
                len(shrunk),
            )
            return TgAudioPayload(
                TgAudioKind.VOICE,
                shrunk,
                f"{filename_base}.ogg",
                duration,
            )

    mp3 = bytes_to_mp3(ogg, suffix=".ogg")
    if not mp3:
        logger.error("prepare_ogg_bytes: mp3 conversion failed (%s bytes)", len(ogg))
        return None

    logger.info(
        "ogg %s bytes not suitable for TG voice — sending mp3 document",
        len(ogg),
    )
    return TgAudioPayload(
        TgAudioKind.DOCUMENT,
        mp3,
        f"{filename_base}.mp3",
        duration,
    )


def is_voice_forbidden_error(exc: Exception) -> bool:
    if not isinstance(exc, TelegramBadRequest):
        return False
    text = str(exc)
    return "VOICE_MESSAGES_FORBIDDEN" in text or "voice messages forbidden" in text.lower()


async def send_tg_audio_payload(
    chat_id: int,
    payload: TgAudioPayload,
    *,
    bot: Optional[Bot] = None,
    message: Optional[Message] = None,
    caption: Optional[str] = None,
    message_thread_id: Optional[int] = None,
    reply_markup: Any = None,
    parse_mode: Any = None,
    **extra: Any,
) -> Any:
    """Отправить voice или MP3-документ. При запрете voice — fallback на document."""
    kwargs: dict[str, Any] = dict(extra)
    if caption is not None:
        kwargs["caption"] = caption
    if message_thread_id is not None:
        kwargs["message_thread_id"] = message_thread_id
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    if parse_mode is not None:
        kwargs["parse_mode"] = parse_mode

    async def _send_voice(data: bytes, filename: str, duration: Optional[int]) -> Any:
        file = BufferedInputFile(data, filename=filename)
        vk = dict(kwargs)
        if duration is not None:
            vk["duration"] = duration
        if bot:
            return await bot.send_voice(chat_id, file, **vk)
        if message:
            return await message.answer_voice(file, **vk)
        raise RuntimeError("send_tg_audio_payload: bot or message required")

    async def _send_document(data: bytes, filename: str) -> Any:
        file = BufferedInputFile(data, filename=filename)
        if bot:
            return await bot.send_document(chat_id, file, **kwargs)
        if message:
            return await message.answer_document(file, **kwargs)
        raise RuntimeError("send_tg_audio_payload: bot or message required")

    if payload.kind == TgAudioKind.VOICE:
        try:
            return await _send_voice(payload.data, payload.filename, payload.duration_sec)
        except TelegramBadRequest as e:
            if not is_voice_forbidden_error(e):
                raise
            logger.info("voice forbidden chat=%s — mp3 document fallback", chat_id)
            mp3 = bytes_to_mp3(payload.data, suffix=".ogg")
            if not mp3:
                raise
            return await _send_document(mp3, payload.filename.replace(".ogg", ".mp3"))

    return await _send_document(payload.data, payload.filename)
