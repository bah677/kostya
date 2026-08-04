"""ElevenLabs + atempo + фон — как в Библии; на выходе WAV для видео + OGG для превью."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Sequence, Tuple

import aiohttp

from youtube_prayer.tts_text import format_prayer_for_tts

logger = logging.getLogger(__name__)

_MAX_CHARS = 5000
_TIMEOUT_SEC = 120.0
_AUDIO_EXTS = {".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".webm"}
_DEFAULT_BG_VOLUME = 0.14
_TAIL_RESERVE_SEC = 60.0
_MIN_TRACK_FOR_RANDOM_SEC = 90.0


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def resolve_atempo() -> float:
    raw = _env("PRAYER_TTS_ATEMPO") or _env("YT_PRAYER_ATEMPO") or "0.85"
    try:
        v = float(raw)
    except ValueError:
        v = 0.85
    return max(0.5, min(1.2, v))


def resolve_bg_music_dir() -> Path:
    raw = _env("PRAYER_BG_MUSIC_DIR") or _env("YT_PRAYER_BG_MUSIC_DIR")
    if raw:
        return Path(raw).expanduser()
    for cand in (
        Path("/home/appuser/biblia/data/prayer_bg_music"),
        Path("/home/appuser/dev/kostya/biblia/data/prayer_bg_music"),
    ):
        if cand.is_dir():
            return cand
    return Path("/home/appuser/biblia/data/prayer_bg_music")


def resolve_bg_volume() -> float:
    raw = _env("PRAYER_BG_MUSIC_VOLUME") or str(_DEFAULT_BG_VOLUME)
    try:
        v = float(raw)
    except ValueError:
        v = _DEFAULT_BG_VOLUME
    return max(0.02, min(0.5, v))


def probe_duration_sec(path: Path) -> Optional[float]:
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


def list_bg_tracks(music_dir: Optional[Path] = None) -> list[Path]:
    root = music_dir or resolve_bg_music_dir()
    if not root.is_dir():
        return []
    return [
        p
        for p in sorted(root.iterdir())
        if p.is_file() and p.suffix.lower() in _AUDIO_EXTS and p.stat().st_size > 10_000
    ]


async def _elevenlabs_mp3(
    text: str,
    *,
    voice_id: Optional[str] = None,
    lang: str = "ru",
) -> bytes:
    api_key = _env("ELEVENLABS_API_KEY")
    vid = (voice_id or _env("ELEVENLABS_VOICE_ID")).strip()
    model_id = _env("ELEVENLABS_MODEL_ID") or "eleven_flash_v2_5"
    output_format = _env("ELEVENLABS_OUTPUT_FORMAT") or "mp3_44100_128"
    try:
        stability = float(_env("ELEVENLABS_STABILITY") or "0.45")
    except ValueError:
        stability = 0.45
    try:
        similarity = float(_env("ELEVENLABS_SIMILARITY") or "0.75")
    except ValueError:
        similarity = 0.75

    if not api_key or not vid:
        raise RuntimeError("ELEVENLABS_API_KEY / voice_id не заданы")

    body = format_prayer_for_tts(text, lang=lang)
    if not body:
        raise ValueError("empty prayer text after format")
    if len(body) > _MAX_CHARS:
        body = body[:_MAX_CHARS]

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}"
    payload = {
        "text": body,
        "model_id": model_id,
        "voice_settings": {
            "stability": stability,
            "similarity_boost": similarity,
        },
    }
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SEC)
    logger.info(
        "ElevenLabs TTS chars=%s model=%s voice=%s lang=%s",
        len(body),
        model_id,
        vid[:8],
        lang,
    )
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            url,
            params={"output_format": output_format},
            headers=headers,
            json=payload,
        ) as resp:
            raw = await resp.read()
            if resp.status >= 400:
                err = raw.decode("utf-8", errors="replace")[:400]
                raise RuntimeError(f"ElevenLabs HTTP {resp.status}: {err}")
            if not raw:
                raise RuntimeError("ElevenLabs empty body")
            return raw


def _mix_voice_bg_to_files(
    voice_mp3: bytes,
    *,
    out_wav: Path,
    out_ogg: Optional[Path] = None,
    atempo: float,
    music_dir: Optional[Path] = None,
) -> float:
    """Пишет WAV (для видео). Опционально OGG. Возвращает длительность сек."""
    tracks = list_bg_tracks(music_dir)
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    tempo = max(0.5, min(1.2, float(atempo)))
    vol = resolve_bg_volume()

    with tempfile.TemporaryDirectory(prefix="yt_prayer_aud_") as tmp:
        root = Path(tmp)
        voice_path = root / "voice.mp3"
        voice_path.write_bytes(voice_mp3)

        if tracks:
            track = random.choice(tracks)
            duration = probe_duration_sec(track) or 0.0
            if duration < _MIN_TRACK_FOR_RANDOM_SEC:
                start_sec = 0.0
            else:
                max_start = max(0.0, duration - _TAIL_RESERVE_SEC)
                start_sec = random.uniform(0.0, max_start) if max_start > 1.0 else 0.0
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
                "-c:a",
                "pcm_s16le",
                str(out_wav),
            ]
            logger.info("bg mix track=%s start=%.1f atempo=%.2f", track.name, start_sec, tempo)
        else:
            logger.warning("нет bg-треков в %s — только голос", resolve_bg_music_dir())
            af = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=mono"
            if abs(tempo - 1.0) >= 0.001:
                af = f"atempo={tempo:.4f}," + af
            cmd = [
                ffmpeg,
                "-y",
                "-i",
                str(voice_path),
                "-vn",
                "-af",
                af,
                "-c:a",
                "pcm_s16le",
                str(out_wav),
            ]

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
        if proc.returncode != 0 or not out_wav.is_file():
            raise RuntimeError(f"ffmpeg mix failed: {(proc.stderr or '')[-500:]}")

        if out_ogg is not None:
            ogg_cmd = [
                ffmpeg,
                "-y",
                "-i",
                str(out_wav),
                "-c:a",
                "libopus",
                "-b:a",
                "24k",
                "-ar",
                "48000",
                "-ac",
                "1",
                str(out_ogg),
            ]
            subprocess.run(ogg_cmd, capture_output=True, text=True, timeout=180, check=False)

        dur = probe_duration_sec(out_wav) or 0.0
        return dur


async def synthesize_prayer_audio(
    prayer_text: str,
    *,
    work_dir: Path,
    voice_id: Optional[str] = None,
    lang: str = "ru",
) -> Tuple[Path, Optional[Path], float, str]:
    """
    Returns: (wav_path, ogg_path|None, duration_sec, tts_text)
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    tts_text = format_prayer_for_tts(prayer_text, lang=lang)
    mp3 = await _elevenlabs_mp3(prayer_text, voice_id=voice_id, lang=lang)
    wav_path = work_dir / "prayer_mixed.wav"
    ogg_path = work_dir / "prayer_mixed.ogg"
    dur = await asyncio.to_thread(
        _mix_voice_bg_to_files,
        mp3,
        out_wav=wav_path,
        out_ogg=ogg_path,
        atempo=resolve_atempo(),
    )
    if not ogg_path.is_file() or ogg_path.stat().st_size < 200:
        ogg_path = None  # type: ignore[assignment]
    return wav_path, ogg_path, dur, tts_text
