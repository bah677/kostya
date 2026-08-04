"""OGG Opus для Telegram voice: волна + scrub (Bot API лимит ~1 МиБ на waveform)."""

from __future__ import annotations

import logging
import math
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

# Bot API не генерирует waveform для voice > 1 МиБ (tdlib/telegram-bot-api#354).
TG_VOICE_WAVEFORM_MAX_BYTES = 1024 * 1024


def ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_bin() -> str:
    return shutil.which("ffprobe") or "ffprobe"


def probe_media_duration_sec(path: Union[str, Path]) -> Optional[float]:
    try:
        proc = subprocess.run(
            [
                ffprobe_bin(),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode != 0:
            return None
        return float((proc.stdout or "").strip())
    except Exception:
        return None


def ogg_path_duration_sec(path: Union[str, Path]) -> Optional[int]:
    """Целые секунды для sendVoice(duration=…)."""
    sec = probe_media_duration_sec(path)
    if sec is None or sec <= 0:
        return None
    return max(1, int(math.ceil(sec)))


def ogg_bytes_duration_sec(ogg: bytes) -> Optional[int]:
    if not ogg or len(ogg) < 200:
        return None
    with tempfile.TemporaryDirectory(prefix="ogg_dur_") as tmp:
        p = Path(tmp) / "v.ogg"
        p.write_bytes(ogg)
        return ogg_path_duration_sec(p)


def opus_bitrate_for_tg_waveform(
    duration_sec: float,
    *,
    max_bytes: int = TG_VOICE_WAVEFORM_MAX_BYTES,
    floor_kbps: int = 12,
    ceil_kbps: int = 48,
) -> str:
    if duration_sec <= 0:
        return f"{ceil_kbps}k"
    budget_bits = max_bytes * 0.90 * 8.0
    kbps = int(budget_bits / duration_sec / 1000.0)
    kbps = max(floor_kbps, min(ceil_kbps, kbps))
    return f"{kbps}k"


def max_chunk_sec_for_waveform(
    *,
    bitrate_kbps: int = 16,
    max_bytes: int = TG_VOICE_WAVEFORM_MAX_BYTES,
) -> float:
    """Макс. длина одного voice-чанка, чтобы уложиться в лимит волны."""
    return (max_bytes * 0.90 * 8.0) / (max(8, bitrate_kbps) * 1000.0)


def libopus_voice_args(*, bitrate: str) -> list[str]:
    return [
        "-c:a",
        "libopus",
        "-b:a",
        bitrate,
        "-vbr",
        "on",
        "-compression_level",
        "10",
        "-frame_duration",
        "60",
        "-application",
        "voip",
        "-ar",
        "48000",
        "-ac",
        "1",
    ]


def shrink_ogg_under_limit(
    src: Path,
    dst: Path,
    *,
    duration_sec: float,
) -> bool:
    """Второй проход, если VBR вылез за 1 МиБ."""
    br = opus_bitrate_for_tg_waveform(
        duration_sec,
        max_bytes=int(TG_VOICE_WAVEFORM_MAX_BYTES * 0.82),
        floor_kbps=10,
        ceil_kbps=24,
    )
    cmd = [
        ffmpeg_bin(),
        "-y",
        "-i",
        str(src),
        "-vn",
        *libopus_voice_args(bitrate=br),
        str(dst),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600, check=False
        )
        if proc.returncode != 0:
            logger.error("shrink ogg failed: %s", (proc.stderr or "")[-500:])
            return False
        return dst.is_file() and dst.stat().st_size > 500
    except Exception as e:
        logger.exception("shrink ogg: %s", e)
        return False
