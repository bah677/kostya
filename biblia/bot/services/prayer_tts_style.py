"""Общий стиль и постобработка озвучки молитв (instruct + atempo → OGG Opus)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_PRAYER_TTS_INSTRUCT = (
    "Warm natural prayerful speech, gentle rhythm, slight emotional variation, "
    "not monotone and not robotic. Soft unhurried pace. "
    "The final word амИнь: stress on capital И (a-MÍN), clear and solemn."
)

DEFAULT_PRAYER_TTS_ATEMPO = 0.92


def resolve_prayer_tts_instruct() -> str:
    """Единый instruct для всех движков, где он поддерживается."""
    from config import config

    for key in ("PRAYER_TTS_INSTRUCT", "VOICEBOX_INSTRUCT", "OPENAI_TTS_INSTRUCT"):
        val = (getattr(config, key, None) or "").strip()
        if val:
            return val
    return DEFAULT_PRAYER_TTS_INSTRUCT


def resolve_prayer_tts_atempo() -> float:
    """Единый atempo (ffmpeg) после синтеза — одинаковый темп у всех движков."""
    from config import config

    raw = getattr(config, "PRAYER_TTS_ATEMPO", None)
    if raw is None or raw == "":
        raw = getattr(config, "VOICEBOX_ATEMPO", None)
    try:
        v = float(raw if raw is not None else DEFAULT_PRAYER_TTS_ATEMPO)
    except (TypeError, ValueError):
        v = DEFAULT_PRAYER_TTS_ATEMPO
    return max(0.5, min(1.2, v))


def audio_bytes_to_ogg_opus(
    audio_bytes: bytes,
    *,
    atempo: Optional[float] = None,
    prefix: str = "prayer_tts_",
) -> Optional[bytes]:
    """Любой вход, который читает ffmpeg → OGG Opus с общим atempo."""
    if not audio_bytes:
        return None
    tempo = (
        resolve_prayer_tts_atempo()
        if atempo is None
        else max(0.5, min(1.2, float(atempo)))
    )
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        root = Path(tmp)
        in_path = root / "in.bin"
        ogg_path = root / "out.ogg"
        in_path.write_bytes(audio_bytes)
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(in_path),
            "-vn",
            "-filter:a",
            f"atempo={tempo:.4f}",
            "-c:a",
            "libopus",
            "-b:a",
            "96k",
            "-vbr",
            "on",
            "-application",
            "audio",
            str(ogg_path),
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=180, check=False
            )
            if proc.returncode != 0:
                logger.error("ffmpeg prayer tts: %s", (proc.stderr or "")[-500:])
                return None
            if not ogg_path.is_file() or ogg_path.stat().st_size < 200:
                return None
            return ogg_path.read_bytes()
        except Exception as e:
            logger.exception("ffmpeg prayer tts convert: %s", e)
            return None
