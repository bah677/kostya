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
from typing import List, Optional, Sequence, Tuple

import aiohttp

from youtube_prayer.tts_text import format_prayer_for_tts

logger = logging.getLogger(__name__)

_MAX_CHARS = 5000
_TIMEOUT_SEC = 120.0
_AUDIO_EXTS = {".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".webm"}
_DEFAULT_BG_VOLUME = 0.14
_TAIL_RESERVE_SEC = 60.0
_MIN_TRACK_FOR_RANDOM_SEC = 90.0

WordTiming = Tuple[float, float, str]  # start, end, word


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
        Path("/home/appuser/biblia/assets/prayer_bg_music"),
        Path("/home/appuser/dev/kostya/biblia/assets/prayer_bg_music"),
        Path("/home/appuser/biblia/data/prayer_bg_music"),
        Path("/home/appuser/dev/kostya/biblia/data/prayer_bg_music"),
    ):
        if cand.is_dir():
            return cand
    return Path("/home/appuser/biblia/assets/prayer_bg_music")


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
    stress_amen: bool = True,
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
    body = format_prayer_for_tts(text, lang=lang, stress_amen=stress_amen)
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
            try:
                from bot.services.llm_usage_tracker import log_elevenlabs_tts

                await log_elevenlabs_tts(
                    model_id=model_id,
                    chars=len(body),
                    voice_id=vid,
                    request_kind="tts",
                )
            except Exception:
                pass
            return raw


async def _elevenlabs_mp3_with_timings(
    text: str,
    *,
    voice_id: Optional[str] = None,
    lang: str = "ru",
    stress_amen: bool = True,
) -> Tuple[bytes, List[WordTiming]]:
    """
    TTS + character alignment (тот же тариф, что обычный TTS).
    Возвращает (mp3_bytes, word_timings до atempo).
    """
    import base64
    import json

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

    body = format_prayer_for_tts(text, lang=lang, stress_amen=stress_amen)
    if not body:
        raise ValueError("empty prayer text after format")
    if len(body) > _MAX_CHARS:
        body = body[:_MAX_CHARS]

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}/with-timestamps"
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
        "Accept": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SEC)
    logger.info(
        "ElevenLabs TTS+timestamps chars=%s model=%s voice=%s lang=%s",
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
                # fallback на обычный TTS без таймкодов
                logger.warning(
                    "ElevenLabs with-timestamps HTTP %s — fallback plain TTS: %s",
                    resp.status,
                    err,
                )
                mp3 = await _elevenlabs_mp3(
                    text, voice_id=voice_id, lang=lang, stress_amen=stress_amen
                )
                return mp3, []
            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception as e:
                raise RuntimeError(f"ElevenLabs timestamps JSON parse: {e}") from e

    b64 = data.get("audio_base64") or ""
    if not b64:
        raise RuntimeError("ElevenLabs with-timestamps: empty audio_base64")
    mp3 = base64.b64decode(b64)
    alignment = data.get("normalized_alignment") or data.get("alignment") or {}
    timings = _words_from_char_alignment(alignment)
    try:
        from bot.services.llm_usage_tracker import log_elevenlabs_tts

        await log_elevenlabs_tts(
            model_id=model_id,
            chars=len(body),
            voice_id=vid,
            request_kind="tts_with_timestamps",
        )
    except Exception:
        pass
    return mp3, timings


def _words_from_char_alignment(alignment: dict) -> List[WordTiming]:
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    if not (chars and starts and ends) or not (
        len(chars) == len(starts) == len(ends)
    ):
        return []
    words: List[WordTiming] = []
    buf: List[str] = []
    w_start: Optional[float] = None
    w_end: float = 0.0
    for ch, st, en in zip(chars, starts, ends):
        if ch.isspace():
            if buf and w_start is not None:
                words.append((float(w_start), float(w_end), "".join(buf)))
                buf = []
                w_start = None
            continue
        if w_start is None:
            w_start = float(st)
        buf.append(ch)
        w_end = float(en)
    if buf and w_start is not None:
        words.append((float(w_start), float(w_end), "".join(buf)))
    return words


def scale_word_timings(
    timings: Sequence[WordTiming], *, atempo: float
) -> List[WordTiming]:
    """После atempo голос длиннее при tempo<1 → умножаем времена на 1/tempo."""
    tempo = max(0.5, min(1.2, float(atempo)))
    if abs(tempo - 1.0) < 0.001:
        return [(float(s), float(e), w) for s, e, w in timings]
    f = 1.0 / tempo
    return [(float(s) * f, float(e) * f, w) for s, e, w in timings]


def cue_chunks_from_words(
    timings: Sequence[WordTiming],
    *,
    words_per_cue: int = 5,
) -> List[WordTiming]:
    """Группирует слова в субтитровые реплики (start, end, text)."""
    words = [(float(s), float(e), w) for s, e, w in timings if (w or "").strip()]
    if not words:
        return []
    from youtube_prayer.render import group_words_into_cues

    out: List[WordTiming] = []
    for group in group_words_into_cues(words, words_per_cue=words_per_cue):
        out.append(
            (
                group[0][0],
                max(group[0][0] + 0.35, group[-1][1]),
                " ".join(g[2] for g in group),
            )
        )
    return out


def _voice_output_duration_sec(voice_in_dur: float, tempo: float) -> float:
    """Длительность голоса после atempo (сек)."""
    if voice_in_dur <= 0:
        return 0.0
    if abs(tempo - 1.0) < 0.001 or tempo <= 0:
        return voice_in_dur
    return voice_in_dur / tempo


def outro_tail_sec() -> float:
    """Хвост после молитвы — под концевую карточку в ролике.

    Карточка с адресом бота — единственная часть воронки, которую видят все
    зрители. Описание в плеере Shorts свёрнуто: за 37 тысяч просмотров по
    ссылке из него не пришёл ни один человек (касаний yt_* в базе клуба — 0,
    при том что код метки в проде и ссылка в описании стоит).

    Хвост нужен, чтобы карточка не наезжала на последние слова молитвы.
    Фоновая музыка под ним продолжает играть: amix обрезается по голосу,
    а голос к этому моменту уже дополнен тишиной.
    """
    try:
        v = float(os.getenv("YT_PRAYER_OUTRO_SEC") or 2.5)
    except (TypeError, ValueError):
        v = 2.5
    return max(0.0, min(5.0, v))


def _mix_filter_complex(*, tempo: float, vol: float, outro_sec: float = 0.0) -> str:
    """
    Голос + тихий фон: фон зациклен на всю длину голоса.
    duration=first + dropout_transition=0 — голос не затухает, когда фон кончился.
    """
    voice_chain = f"atempo={tempo:.4f}," if abs(tempo - 1.0) >= 0.001 else ""
    if outro_sec > 0.05:
        voice_chain += f"apad=pad_dur={outro_sec:.2f},"
    bg_chain = (
        f"aloop=loop=-1:size=2e+09,volume={vol:.4f},"
        f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=mono"
    )
    voice_fmt = f"{voice_chain}aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=mono"
    return (
        f"[0:a]{voice_fmt}[v];"
        f"[1:a]{bg_chain}[bg];"
        f"[v][bg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
    )


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

        voice_in_dur = probe_duration_sec(voice_path) or 0.0
        outro = outro_tail_sec()
        out_dur = _voice_output_duration_sec(voice_in_dur, tempo) + outro

        if tracks:
            track = random.choice(tracks)
            duration = probe_duration_sec(track) or 0.0
            if duration < _MIN_TRACK_FOR_RANDOM_SEC:
                start_sec = 0.0
            else:
                # Запас под длину голоса (не только 60 с), но aloop всё равно подстрахует.
                reserve = max(_TAIL_RESERVE_SEC, out_dur + 15.0)
                max_start = max(0.0, duration - reserve)
                start_sec = random.uniform(0.0, max_start) if max_start > 1.0 else 0.0
            filter_complex = _mix_filter_complex(
                tempo=tempo, vol=vol, outro_sec=outro
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
            logger.info(
                "bg mix track=%s start=%.1f atempo=%.2f out_dur=%.1f",
                track.name,
                start_sec,
                tempo,
                out_dur,
            )
        else:
            logger.warning("нет bg-треков в %s — только голос", resolve_bg_music_dir())
            af = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=mono"
            if outro > 0.05:
                af = f"apad=pad_dur={outro:.2f}," + af
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
    stress_amen: bool = True,
) -> Tuple[Path, Optional[Path], float, str, List[WordTiming]]:
    """
    Returns: (wav_path, ogg_path|None, duration_sec, tts_text, word_timings)
    word_timings уже с учётом atempo.
    """
    import json

    work_dir.mkdir(parents=True, exist_ok=True)
    tts_text = format_prayer_for_tts(prayer_text, lang=lang, stress_amen=stress_amen)
    tempo = resolve_atempo()
    mp3, raw_timings = await _elevenlabs_mp3_with_timings(
        prayer_text, voice_id=voice_id, lang=lang, stress_amen=stress_amen
    )
    wav_path = work_dir / "prayer_mixed.wav"
    ogg_path = work_dir / "prayer_mixed.ogg"
    dur = await asyncio.to_thread(
        _mix_voice_bg_to_files,
        mp3,
        out_wav=wav_path,
        out_ogg=ogg_path,
        atempo=tempo,
    )
    if not ogg_path.is_file() or ogg_path.stat().st_size < 200:
        ogg_path = None  # type: ignore[assignment]
    timings = scale_word_timings(raw_timings, atempo=tempo)
    try:
        (work_dir / "word_timings.json").write_text(
            json.dumps(
                [{"start": s, "end": e, "word": w} for s, e, w in timings],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
    logger.info(
        "TTS ready dur=%.1f words=%s atempo=%.2f",
        dur,
        len(timings),
        tempo,
    )
    return wav_path, ogg_path, dur, tts_text, timings
