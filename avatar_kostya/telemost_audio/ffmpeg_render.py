"""Нарезка аудио в OGG Opus для голосовых сообщений Telegram."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import List, Sequence

from config import config
from telemost_audio.moments_llm import AudioClipMoment
from telemost_audio.telegram_voice_opus import (
    TG_VOICE_WAVEFORM_MAX_BYTES,
    ffmpeg_bin,
    libopus_voice_args,
    ogg_path_duration_sec,
    opus_bitrate_for_tg_waveform,
    probe_media_duration_sec,
    shrink_ogg_under_limit,
)

logger = logging.getLogger(__name__)


def _timing_offset_sec() -> float:
    return float(
        getattr(config, "TELEMOST_AUDIO_CLIPS_OFFSET_SEC", -0.5) or -0.5
    )


def _ensure_under_waveform_limit(path: Path, duration_sec: float) -> Path:
    if not path.is_file() or path.stat().st_size <= TG_VOICE_WAVEFORM_MAX_BYTES:
        return path
    shrunk = path.with_name(path.stem + "_1m.ogg")
    if shrink_ogg_under_limit(path, shrunk, duration_sec=duration_sec):
        if shrunk.stat().st_size < path.stat().st_size:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            shrunk.rename(path)
            return path
    if path.stat().st_size > TG_VOICE_WAVEFORM_MAX_BYTES:
        logger.warning(
            "voice ogg %s still %s bytes >1MiB — TG без волны",
            path.name,
            path.stat().st_size,
        )
    return path


def _render_one_sync(
    audio_path: Path,
    moment: AudioClipMoment,
    out_path: Path,
    *,
    max_duration_sec: float,
) -> bool:
    offset = _timing_offset_sec()
    start = max(0.0, moment.start_sec + offset)
    end = min(moment.end_sec + offset, start + max_duration_sec)
    duration = end - start
    if duration < 35:
        return False

    bitrate = opus_bitrate_for_tg_waveform(duration)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin(),
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(audio_path),
        "-t",
        f"{duration:.3f}",
        "-vn",
        *libopus_voice_args(bitrate=bitrate),
        str(out_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if proc.returncode != 0:
            logger.error(
                "ffmpeg audio clip %s: %s",
                out_path.name,
                (proc.stderr or "")[-600:],
            )
            return False
        if not (out_path.is_file() and out_path.stat().st_size > 5000):
            return False
        _ensure_under_waveform_limit(out_path, duration)
        logger.info(
            "audio clip ok %s dur=%.1fs bitrate=%s bytes=%s",
            out_path.name,
            duration,
            bitrate,
            out_path.stat().st_size,
        )
        return True
    except Exception as e:
        logger.exception("ffmpeg audio render: %s", e)
        return False


async def render_audio_clips(
    audio_path: str,
    moments: Sequence[AudioClipMoment],
    *,
    work_dir: str | Path,
    max_duration_sec: int = 120,
) -> List[Path]:
    src = Path(audio_path)
    if not src.is_file():
        return []
    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    out_paths: List[Path] = []
    max_dur = float(max_duration_sec)
    for i, moment in enumerate(moments, start=1):
        out = root / f"voice_{i:02d}.ogg"
        ok = await asyncio.to_thread(
            _render_one_sync,
            src,
            moment,
            out,
            max_duration_sec=max_dur,
        )
        if ok:
            out_paths.append(out)
    return out_paths


def _render_segment_sync(
    audio_path: Path,
    out_path: Path,
    *,
    start_sec: float,
    duration_sec: float,
    bitrate: str,
    enforce_waveform_limit: bool = False,
) -> bool:
    if duration_sec < 1.0:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin(),
        "-y",
        "-ss",
        f"{start_sec:.3f}",
        "-i",
        str(audio_path),
        "-t",
        f"{duration_sec:.3f}",
        "-vn",
        *libopus_voice_args(bitrate=bitrate),
        str(out_path),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=3600, check=False
        )
        if proc.returncode != 0:
            logger.error(
                "ffmpeg full voice %s: %s",
                out_path.name,
                (proc.stderr or "")[-800:],
            )
            return False
        if not (out_path.is_file() and out_path.stat().st_size > 5000):
            return False
        if enforce_waveform_limit:
            _ensure_under_waveform_limit(out_path, duration_sec)
        logger.info(
            "full voice ok %s start=%.1f dur=%.1fs bitrate=%s bytes=%s",
            out_path.name,
            start_sec,
            duration_sec,
            bitrate,
            out_path.stat().st_size,
        )
        return True
    except Exception as e:
        logger.exception("ffmpeg full voice: %s", e)
        return False


# Лимит Bot API на upload voice (~50 МиБ); волна для полной записи не цель.
_TG_VOICE_UPLOAD_MAX_BYTES = 48 * 1024 * 1024


def _render_full_voice_parts_sync(
    audio_path: Path,
    out_dir: Path,
    *,
    stem: str,
) -> List[Path]:
    """
    Вся запись → один OGG Opus.
    Не режем под волну (1 МиБ): приоритет — цельный эфир; duration для scrub.
    Битрейт снижаем только если упираемся в лимит upload TG (~50 МиБ).
    """
    total = probe_media_duration_sec(audio_path) or 0.0
    if total <= 0:
        logger.error("full voice: unknown duration %s", audio_path)
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{stem}.ogg"
    for br in ("32k", "24k", "16k"):
        if not _render_segment_sync(
            audio_path,
            out,
            start_sec=0.0,
            duration_sec=total,
            bitrate=br,
            enforce_waveform_limit=False,
        ):
            continue
        size = out.stat().st_size
        if size <= _TG_VOICE_UPLOAD_MAX_BYTES:
            if size > TG_VOICE_WAVEFORM_MAX_BYTES:
                logger.info(
                    "full voice %s %s bytes >1MiB — без волны TG, файл цельный",
                    out.name,
                    size,
                )
            return [out]
        logger.warning(
            "full voice %s too large (%s) @%s — retry lower bitrate",
            out.name,
            size,
            br,
        )
    if out.is_file() and out.stat().st_size > 5000:
        return [out]
    return []


async def render_full_voice_ogg(
    audio_path: str,
    *,
    work_dir: str | Path,
    stem: str = "full_voice",
) -> Path | None:
    parts = await render_full_voice_ogg_parts(
        audio_path, work_dir=work_dir, stem=stem
    )
    return parts[0] if parts else None


async def render_full_voice_ogg_parts(
    audio_path: str,
    *,
    work_dir: str | Path,
    stem: str = "full_voice",
) -> List[Path]:
    src = Path(audio_path)
    if not src.is_file():
        return []
    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    return await asyncio.to_thread(
        _render_full_voice_parts_sync, src, root, stem=stem
    )


# re-export for callers that need duration on sendVoice
__all__ = [
    "render_audio_clips",
    "render_full_voice_ogg",
    "render_full_voice_ogg_parts",
    "ogg_path_duration_sec",
]
