"""Тихий фон под голос молитвы: случайный трек + случайный offset → ffmpeg mix."""

from __future__ import annotations

import logging
import random
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Sequence

from bot.services.prayer_tts_style import (
    TG_VOICE_WAVEFORM_MAX_BYTES,
    libopus_voice_args,
    opus_bitrate_for_tg_waveform,
    probe_media_duration_sec,
)

logger = logging.getLogger(__name__)

_AUDIO_EXTS = {".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".webm"}
_DEFAULT_VOLUME = 0.14
# Не начинать слишком близко к концу трека (запас под длинную молитву).
_TAIL_RESERVE_SEC = 60.0
_MIN_TRACK_FOR_RANDOM_SEC = 90.0


def resolve_prayer_bg_music_dir() -> Path:
    from config import config

    raw = (getattr(config, "PRAYER_BG_MUSIC_DIR", None) or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path("/home/appuser/biblia/data/prayer_bg_music")


def resolve_prayer_bg_music_volume() -> float:
    from config import config

    raw = getattr(config, "PRAYER_BG_MUSIC_VOLUME", None)
    try:
        v = float(raw if raw is not None else _DEFAULT_VOLUME)
    except (TypeError, ValueError):
        v = _DEFAULT_VOLUME
    return max(0.02, min(0.5, v))


def list_bg_tracks(music_dir: Optional[Path] = None) -> list[Path]:
    root = music_dir or resolve_prayer_bg_music_dir()
    if not root.is_dir():
        return []
    files = [
        p
        for p in sorted(root.iterdir())
        if p.is_file() and p.suffix.lower() in _AUDIO_EXTS and p.stat().st_size > 10_000
    ]
    return files


def _pick_track_and_start(
    tracks: Sequence[Path],
) -> tuple[Path, float]:
    track = random.choice(list(tracks))
    duration = probe_media_duration_sec(track) or 0.0
    if duration < _MIN_TRACK_FOR_RANDOM_SEC:
        return track, 0.0
    max_start = max(0.0, duration - _TAIL_RESERVE_SEC)
    start = random.uniform(0.0, max_start) if max_start > 1.0 else 0.0
    return track, start


def mix_voice_with_bg_music(
    voice_audio: bytes,
    *,
    music_dir: Optional[Path] = None,
    volume: Optional[float] = None,
    atempo: float = 1.0,
    bitrate: Optional[str] = None,
    voice_suffix: str = ".bin",
) -> Optional[bytes]:
    """
    Накладывает тихий фон под голос. Длина = длина голоса.
    Один проход ffmpeg (atempo + mix → libopus VBR), без двойного перекодирования.
    Битрейт целится в <1 МБ — иначе Bot API отдаёт voice без волны/scrub.
    При ошибке/отсутствии треков возвращает None.
    """
    if not voice_audio:
        return None
    tracks = list_bg_tracks(music_dir)
    if not tracks:
        logger.warning(
            "prayer bg music: нет треков в %s",
            music_dir or resolve_prayer_bg_music_dir(),
        )
        return None

    track, start_sec = _pick_track_and_start(tracks)
    vol = (
        resolve_prayer_bg_music_volume()
        if volume is None
        else max(0.02, min(0.5, float(volume)))
    )
    tempo = max(0.5, min(1.2, float(atempo)))
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    suffix = voice_suffix if voice_suffix.startswith(".") else f".{voice_suffix}"

    with tempfile.TemporaryDirectory(prefix="prayer_bg_") as tmp:
        root = Path(tmp)
        voice_path = root / f"voice{suffix}"
        out_path = root / "mixed.ogg"
        voice_path.write_bytes(voice_audio)

        in_dur = probe_media_duration_sec(voice_path) or 0.0
        out_dur = in_dur / tempo if tempo > 0 and in_dur > 0 else in_dur
        mix_bitrate = bitrate or opus_bitrate_for_tg_waveform(out_dur)

        # Голос (опц. atempo) + тихий фон; amix по длине голоса; normalize=0.
        voice_chain = f"atempo={tempo:.4f}," if abs(tempo - 1.0) >= 0.001 else ""
        filter_complex = (
            f"[0:a]{voice_chain}aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=mono[v];"
            f"[1:a]volume={vol:.4f},aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=mono[bg];"
            f"[v][bg]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]"
        )
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(voice_path),
            "-ss",
            f"{start_sec:.3f}",
            "-i",
            str(track),
            "-filter_complex",
            filter_complex,
            "-map",
            "[aout]",
            *libopus_voice_args(bitrate=mix_bitrate),
            str(out_path),
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=240, check=False
            )
            if proc.returncode != 0:
                logger.error(
                    "prayer bg mix failed track=%s start=%.1f: %s",
                    track.name,
                    start_sec,
                    (proc.stderr or "")[-600:],
                )
                return None
            if not out_path.is_file() or out_path.stat().st_size < 200:
                return None
            mixed = out_path.read_bytes()
            if len(mixed) > TG_VOICE_WAVEFORM_MAX_BYTES:
                logger.warning(
                    "prayer bg mix %s bytes >1MiB (bitrate=%s dur≈%.1fs) — TG waveform пустая",
                    len(mixed),
                    mix_bitrate,
                    out_dur,
                )
            logger.info(
                "prayer bg mix ok track=%s start=%.1fs vol=%.3f atempo=%.2f bitrate=%s in=%s out=%s",
                track.name,
                start_sec,
                vol,
                tempo,
                mix_bitrate,
                len(voice_audio),
                len(mixed),
            )
            return mixed
        except Exception as e:
            logger.exception("prayer bg mix exception: %s", e)
            return None
