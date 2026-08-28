"""Рендер 16:9 (1024×576) полной молитвы и 3× 9:16 (1080×1920) шортсов."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence, Tuple

logger = logging.getLogger(__name__)

FULL_W = 1024
FULL_H = 576
SHORT_W = 1080
SHORT_H = 1920


@dataclass(frozen=True)
class ShortClip:
    index: int
    path: Path
    start_sec: float
    end_sec: float
    caption: str


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or str(default)).strip())
    except ValueError:
        return default


def _sec_to_ass_time(sec: float) -> str:
    cs = int(round(max(0.0, sec) * 100))
    h = cs // 360_000
    cs %= 360_000
    m = cs // 6_000
    cs %= 6_000
    s = cs // 100
    cs %= 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _wrap_line(text: str, max_chars: int = 28) -> List[str]:
    """Разбить текст субтитра на 1–2 строки."""
    words = " ".join((text or "").split()).split()
    lines: List[str] = []
    cur: List[str] = []
    n = 0
    for w in words:
        add = len(w) + (1 if cur else 0)
        if cur and n + add > max_chars:
            lines.append(" ".join(cur))
            cur = [w]
            n = len(w)
            if len(lines) >= 2:
                break
        else:
            cur.append(w)
            n += add
    if cur and len(lines) < 2:
        lines.append(" ".join(cur))
    return lines[:2]


def _ass_line_break(lines: Sequence[str]) -> str:
    """ASS-перенос: \\N между строками, каждая строка экранируется отдельно."""
    parts = [_ass_escape_text(ln) for ln in lines if (ln or "").strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return "\\N".join(parts)


def _ass_escape_text(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def _write_short_ass(path: Path, chunks: Sequence[Tuple[float, float, str]]) -> None:
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {SHORT_W}
PlayResY: {SHORT_H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Shorts,Arial,52,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,3,0,2,52,52,180,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for start, end, text in chunks:
        if end <= start or not (text or "").strip():
            continue
        wrapped = _ass_line_break(_wrap_line(text))
        if not wrapped:
            continue
        lines.append(
            f"Dialogue: 0,{_sec_to_ass_time(start)},{_sec_to_ass_time(end)},"
            f"Shorts,,0,0,0,,{wrapped}"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _paragraphs(text: str) -> List[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if parts:
        return parts
    flat = " ".join((text or "").split())
    return [flat] if flat else []


def _all_words(prayer_text: str) -> List[str]:
    return " ".join((prayer_text or "").split()).split()


def split_short_windows(
    duration_sec: float,
    prayer_text: str,
    *,
    target_count: int = 3,
    min_sec: float = 35.0,
    max_sec: float = 55.0,
) -> List[Tuple[float, float, str]]:
    """Окна шортсов; caption — слова, попадающие в это окно по пропорциональному таймингу."""
    dur = max(30.0, float(duration_sec))
    words = _all_words(prayer_text)
    if not words:
        words = ["Молитва"]
    n = min(target_count, 3)
    base = dur / n
    windows: List[Tuple[float, float, str]] = []
    for i in range(n):
        start = i * base
        end = dur if i == n - 1 else (i + 1) * base
        length = end - start
        if length > max_sec:
            end = start + max_sec
        elif length < min_sec and end < dur:
            end = min(dur, start + min_sec)
        # слова в окне
        wdur = dur / max(1, len(words))
        chunk_words = [
            w
            for wi, w in enumerate(words)
            if (wi + 1) * wdur > start and wi * wdur < end
        ]
        cap = " ".join(chunk_words[:24]) + ("…" if len(chunk_words) > 24 else "")
        windows.append((start, end, cap or " ".join(words[:12])))
    return windows


def subtitle_chunks_for_window(
    prayer_text: str,
    *,
    full_duration: float,
    window_start: float,
    window_end: float,
    words_per_cue: int = 5,
    offset_sec: float = 0.0,
) -> List[Tuple[float, float, str]]:
    """
    Субтитры, синхронизированные с аудио окна:
    слова молитвы равномерно раскладываются на всю длительность,
    берём только слова внутри [window_start, window_end],
    времена относительно начала клипа (0).
    """
    words = _all_words(prayer_text)
    if not words:
        return [(0.0, max(1.0, window_end - window_start), "")]
    dur = max(1.0, float(full_duration))
    wdur = dur / len(words)
    # список (rel_start, rel_end, word) внутри окна
    timed: List[Tuple[float, float, str]] = []
    for i, w in enumerate(words):
        abs_a = i * wdur
        abs_b = (i + 1) * wdur
        if abs_b <= window_start or abs_a >= window_end:
            continue
        rel_a = max(0.0, abs_a - window_start)
        rel_b = min(window_end - window_start, abs_b - window_start)
        if rel_b > rel_a:
            timed.append((rel_a, rel_b, w))

    if not timed:
        return [(0.0, max(1.0, window_end - window_start), " ".join(words[:8]))]

    chunks: List[Tuple[float, float, str]] = []
    i = 0
    while i < len(timed):
        group = timed[i : i + max(1, words_per_cue)]
        start = group[0][0] + offset_sec
        end = group[-1][1] + offset_sec
        # не уводим за границы клипа
        clip_len = window_end - window_start
        start = max(0.0, min(clip_len - 0.05, start))
        end = max(start + 0.35, min(clip_len, end))
        text = " ".join(g[2] for g in group)
        chunks.append((start, end, text))
        i += max(1, words_per_cue)
    return chunks


def render_horizontal(
    *,
    broll_path: Path,
    audio_wav: Path,
    out_path: Path,
    duration_sec: float,
    width: int = FULL_W,
    height: int = FULL_H,
) -> Path:
    """16:9 фиксированный кадр (по умолчанию 1024×576), без искажения пропорций."""
    ffmpeg = _ffmpeg()
    dur = max(10.0, float(duration_sec))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # broll_montage уже близко к нужному размеру; fill + setsar=1 без искажения
    vf_fill = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps=25,format=yuv420p"
    )
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(broll_path),
        "-i",
        str(audio_wav),
        "-t",
        f"{dur:.3f}",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-vf",
        vf_fill,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-shortest",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    if proc.returncode != 0 or not out_path.is_file():
        raise RuntimeError(f"horizontal render failed: {(proc.stderr or '')[-600:]}")
    logger.info(
        "horizontal ok %s %sx%s bytes=%s",
        out_path.name,
        width,
        height,
        out_path.stat().st_size,
    )
    return out_path


def render_short(
    *,
    horizontal_path: Path,
    out_path: Path,
    start_sec: float,
    end_sec: float,
    prayer_text: str,
    full_duration: float,
    work_dir: Path,
) -> Path:
    """9:16 = 1080×1920, субтитры по словам окна аудио."""
    ffmpeg = _ffmpeg()
    length = max(5.0, end_sec - start_sec)
    ass_path = work_dir / f"{out_path.stem}.ass"
    offset = _env_float("YT_PRAYER_SUBTITLE_OFFSET_SEC", -0.35)
    chunks = subtitle_chunks_for_window(
        prayer_text,
        full_duration=full_duration,
        window_start=start_sec,
        window_end=end_sec,
        words_per_cue=5,
        offset_sec=offset,
    )
    _write_short_ass(ass_path, chunks)

    ass_esc = ass_path.resolve().as_posix().replace("\\", "/").replace(":", "\\:")
    # из 16:9 → 9:16: увеличение + центр-кроп, setsar=1 (без растяжения)
    vf = (
        f"scale={SHORT_W}:{SHORT_H}:force_original_aspect_ratio=increase,"
        f"crop={SHORT_W}:{SHORT_H},setsar=1,"
        f"ass='{ass_esc}'"
    )
    # -ss после -i точнее для A/V sync (медленнее, но субтитры не «плывут»)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(horizontal_path),
        "-ss",
        f"{start_sec:.3f}",
        "-t",
        f"{length:.3f}",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
    if proc.returncode != 0 or not out_path.is_file():
        logger.warning("short with ass failed, retry without: %s", (proc.stderr or "")[-400:])
        vf2 = (
            f"scale={SHORT_W}:{SHORT_H}:force_original_aspect_ratio=increase,"
            f"crop={SHORT_W}:{SHORT_H},setsar=1"
        )
        cmd[cmd.index("-vf") + 1] = vf2
        proc2 = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
        if proc2.returncode != 0 or not out_path.is_file():
            raise RuntimeError(f"short render failed: {(proc2.stderr or '')[-500:]}")
    return out_path


def subtitle_chunks_full(
    prayer_text: str,
    *,
    duration_sec: float,
    words_per_cue: int = 5,
    offset_sec: float = 0.0,
) -> List[Tuple[float, float, str]]:
    """Субтитры на всю длительность вертикального ролика."""
    return subtitle_chunks_for_window(
        prayer_text,
        full_duration=duration_sec,
        window_start=0.0,
        window_end=max(1.0, float(duration_sec)),
        words_per_cue=words_per_cue,
        offset_sec=offset_sec,
    )


def render_vertical_full(
    *,
    broll_path: Path,
    audio_wav: Path,
    out_path: Path,
    duration_sec: float,
    prayer_text: str,
    work_dir: Path,
    width: int = SHORT_W,
    height: int = SHORT_H,
) -> Path:
    """9:16 полная молитва: b-roll + аудио + субтитры по всему тексту."""
    ffmpeg = _ffmpeg()
    dur = max(10.0, float(duration_sec))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path = work_dir / f"{out_path.stem}.ass"
    offset = _env_float("YT_PRAYER_SUBTITLE_OFFSET_SEC", -0.35)
    chunks = subtitle_chunks_full(
        prayer_text,
        duration_sec=dur,
        words_per_cue=5,
        offset_sec=offset,
    )
    _write_short_ass(ass_path, chunks)
    ass_esc = ass_path.resolve().as_posix().replace("\\", "/").replace(":", "\\:")
    vf_fill = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps=25,format=yuv420p,"
        f"ass='{ass_esc}'"
    )
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(broll_path),
        "-i",
        str(audio_wav),
        "-t",
        f"{dur:.3f}",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-vf",
        vf_fill,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-shortest",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    if proc.returncode != 0 or not out_path.is_file():
        logger.warning(
            "vertical with ass failed, retry without subs: %s",
            (proc.stderr or "")[-400:],
        )
        vf_plain = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps=25,format=yuv420p"
        )
        cmd[cmd.index("-vf") + 1] = vf_plain
        proc2 = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
        if proc2.returncode != 0 or not out_path.is_file():
            raise RuntimeError(
                f"vertical render failed: {(proc2.stderr or '')[-600:]}"
            )
    logger.info(
        "vertical ok %s %sx%s bytes=%s",
        out_path.name,
        width,
        height,
        out_path.stat().st_size,
    )
    return out_path


def render_all_shorts(
    *,
    horizontal_path: Path,
    prayer_text: str,
    duration_sec: float,
    work_dir: Path,
) -> List[ShortClip]:
    windows = split_short_windows(duration_sec, prayer_text)
    clips: List[ShortClip] = []
    for i, (start, end, cap) in enumerate(windows, 1):
        out = work_dir / f"short_{i:02d}.mp4"
        render_short(
            horizontal_path=horizontal_path,
            out_path=out,
            start_sec=start,
            end_sec=end,
            prayer_text=prayer_text,
            full_duration=duration_sec,
            work_dir=work_dir,
        )
        clips.append(
            ShortClip(index=i, path=out, start_sec=start, end_sec=end, caption=cap)
        )
    return clips
