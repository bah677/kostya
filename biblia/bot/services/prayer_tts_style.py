"""Общий стиль и постобработка озвучки молитв (instruct + atempo → OGG Opus)."""

from __future__ import annotations

import logging
import math
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_PRAYER_TTS_INSTRUCT = (
    "Warm natural prayerful speech, gentle rhythm, slight emotional variation, "
    "not monotone and not robotic. Clear diction, not dragged. "
    "Honor paragraph breaks as calm breath pauses; brief pause on em dashes. "
    "The final word амИнь: stress on capital И (a-MÍN), clear and solemn."
)

# Prod ElevenLabs: было 0.8, +0.05 к скорости произношения → 0.85.
DEFAULT_PRAYER_TTS_ATEMPO = 0.85

# Bot API не генерирует waveform для voice > 1 МБ (tdlib/telegram-bot-api#354).
TG_VOICE_WAVEFORM_MAX_BYTES = 1024 * 1024


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


def probe_media_duration_sec(path: Path) -> Optional[float]:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    try:
        proc = subprocess.run(
            [
                ffprobe,
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
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            return None
        return float((proc.stdout or "").strip())
    except Exception:
        return None


def ogg_opus_duration_sec(ogg: bytes) -> Optional[int]:
    """Длительность OGG для sendVoice(duration=…); без неё у TG часто duration=0 и нет scrub."""
    if not ogg or len(ogg) < 200:
        return None
    with tempfile.TemporaryDirectory(prefix="ogg_dur_") as tmp:
        path = Path(tmp) / "v.ogg"
        path.write_bytes(ogg)
        sec = probe_media_duration_sec(path)
    if sec is None or sec <= 0:
        return None
    return max(1, int(math.ceil(sec)))


def opus_bitrate_for_tg_waveform(
    duration_sec: float,
    *,
    max_bytes: int = TG_VOICE_WAVEFORM_MAX_BYTES,
    floor_kbps: int = 16,
    ceil_kbps: int = 48,
) -> str:
    """Битрейт под лимит ~1 МБ, чтобы Bot API мог нарисовать волну."""
    if duration_sec <= 0:
        return f"{ceil_kbps}k"
    budget_bits = max_bytes * 0.90 * 8.0
    kbps = int(budget_bits / duration_sec / 1000.0)
    kbps = max(floor_kbps, min(ceil_kbps, kbps))
    return f"{kbps}k"


def libopus_voice_args(*, bitrate: str) -> list[str]:
    """Флаги libopus, совместимые с TG voice (волна + scrub)."""
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


def shrink_ogg_bytes(ogg: bytes) -> Optional[bytes]:
    """Повторное сжатие OGG под лимит волны (~1 МиБ)."""
    if not ogg or len(ogg) < 200:
        return None
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    with tempfile.TemporaryDirectory(prefix="ogg_shrink_") as tmp:
        root = Path(tmp)
        src = root / "in.ogg"
        dst = root / "out.ogg"
        src.write_bytes(ogg)
        dur = probe_media_duration_sec(src) or 0.0
        if dur <= 0:
            return None
        br = opus_bitrate_for_tg_waveform(
            dur,
            max_bytes=int(TG_VOICE_WAVEFORM_MAX_BYTES * 0.82),
            floor_kbps=10,
            ceil_kbps=24,
        )
        cmd = [
            ffmpeg,
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
                logger.error("shrink ogg bytes: %s", (proc.stderr or "")[-500:])
                return None
            if not dst.is_file() or dst.stat().st_size < 200:
                return None
            return dst.read_bytes()
        except Exception as e:
            logger.exception("shrink ogg bytes: %s", e)
            return None


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
        in_dur = probe_media_duration_sec(in_path) or 0.0
        out_dur = in_dur / tempo if tempo > 0 and in_dur > 0 else in_dur
        bitrate = opus_bitrate_for_tg_waveform(out_dur)
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(in_path),
            "-vn",
            "-af",
            f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=mono"
            + (f",atempo={tempo:.4f}" if abs(tempo - 1.0) >= 0.001 else ""),
            *libopus_voice_args(bitrate=bitrate),
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
            data = ogg_path.read_bytes()
            if len(data) > TG_VOICE_WAVEFORM_MAX_BYTES:
                shrunk = shrink_ogg_bytes(data)
                if shrunk and len(shrunk) < len(data):
                    data = shrunk
                if len(data) > TG_VOICE_WAVEFORM_MAX_BYTES:
                    logger.warning(
                        "prayer ogg %s bytes >1MiB (bitrate=%s dur≈%.1fs) — будет MP3-документ",
                        len(data),
                        bitrate,
                        out_dur,
                    )
            return data
        except Exception as e:
            logger.exception("ffmpeg prayer tts convert: %s", e)
            return None
