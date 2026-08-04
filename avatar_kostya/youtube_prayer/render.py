"""Рендер 16:9 полной молитвы и 3× 9:16 шортсов."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence, Tuple

from youtube_prayer.audio_pipeline import probe_duration_sec

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShortClip:
    index: int
    path: Path
    start_sec: float
    end_sec: float
    caption: str


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _sec_to_ass_time(sec: float) -> str:
    cs = int(round(max(0.0, sec) * 100))
    h = cs // 360_000
    cs %= 360_000
    m = cs // 6_000
    cs %= 6_000
    s = cs // 100
    cs %= 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _wrap_line(text: str, max_chars: int = 28) -> str:
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
    return "\\N".join(lines[:2])


def _write_short_ass(path: Path, chunks: Sequence[Tuple[float, float, str]]) -> None:
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Shorts,Arial,54,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,3,0,2,52,52,160,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for start, end, text in chunks:
        if end <= start or not text.strip():
            continue
        wrapped = _wrap_line(text)
        if not wrapped:
            continue
        lines.append(
            f"Dialogue: 0,{_sec_to_ass_time(start)},{_sec_to_ass_time(end)},Shorts,,0,0,0,,{wrapped}"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _paragraphs(text: str) -> List[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if parts:
        return parts
    flat = " ".join((text or "").split())
    return [flat] if flat else []


def split_short_windows(
    duration_sec: float,
    prayer_text: str,
    *,
    target_count: int = 3,
    min_sec: float = 35.0,
    max_sec: float = 55.0,
) -> List[Tuple[float, float, str]]:
    """Окна шортсов по долям абзацев (не тупые трети, если хватает текста)."""
    dur = max(30.0, float(duration_sec))
    paras = _paragraphs(prayer_text)
    if not paras:
        paras = ["Молитва"]
    n = min(target_count, max(1, len(paras)))
    # веса по длине абзацев
    weights = [max(1, len(p.split())) for p in paras]
    total_w = sum(weights) or 1
    # группируем абзацы в n корзин
    buckets: List[List[str]] = [[] for _ in range(n)]
    bucket_w = [0] * n
    bi = 0
    for p, w in zip(paras, weights):
        if bi < n - 1 and bucket_w[bi] > 0 and bucket_w[bi] + w > total_w / n * 1.15:
            bi += 1
        buckets[bi].append(p)
        bucket_w[bi] += w

    windows: List[Tuple[float, float, str]] = []
    # равномерно по времени, но caption из корзины
    base = dur / n
    for i in range(n):
        start = i * base
        end = dur if i == n - 1 else (i + 1) * base
        # поджать к 35–55 если возможно
        length = end - start
        if length > max_sec:
            end = start + max_sec
        elif length < min_sec and end < dur:
            end = min(dur, start + min_sec)
        cap = " ".join(buckets[i]) if buckets[i] else paras[min(i, len(paras) - 1)]
        # для субтитров — первые ~18 слов
        words = cap.split()
        cap_short = " ".join(words[:18]) + ("…" if len(words) > 18 else "")
        windows.append((start, end, cap_short))
    return windows


def render_horizontal(
    *,
    broll_path: Path,
    audio_wav: Path,
    out_path: Path,
    duration_sec: float,
) -> Path:
    """16:9: зацикленный b-roll + аудио."""
    ffmpeg = _ffmpeg()
    dur = max(10.0, float(duration_sec))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # loop broll, scale/crop 1280x720, trim to audio
    filter_v = (
        "scale=1280:720:force_original_aspect_ratio=increase,"
        "crop=1280:720,"
        "fps=25,"
        "format=yuv420p"
    )
    cmd = [
        ffmpeg,
        "-y",
        "-stream_loop",
        "-1",
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
        filter_v,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "26",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    if proc.returncode != 0 or not out_path.is_file():
        raise RuntimeError(f"horizontal render failed: {(proc.stderr or '')[-600:]}")
    logger.info("horizontal ok %s bytes=%s", out_path.name, out_path.stat().st_size)
    return out_path


def render_short(
    *,
    horizontal_path: Path,
    out_path: Path,
    start_sec: float,
    end_sec: float,
    caption: str,
    work_dir: Path,
) -> Path:
    """9:16 кроп центра + ASS."""
    ffmpeg = _ffmpeg()
    length = max(5.0, end_sec - start_sec)
    ass_path = work_dir / f"{out_path.stem}.ass"
    # субтитры на весь клип, смена каждые ~4с кусками caption
    words = caption.split()
    chunks: List[Tuple[float, float, str]] = []
    if not words:
        chunks = [(0.0, length, "")]
    else:
        step = max(3, len(words) // max(1, int(length // 4) or 1))
        t = 0.0
        i = 0
        while i < len(words) and t < length:
            piece = " ".join(words[i : i + step])
            t2 = min(length, t + 4.0)
            chunks.append((t, t2, piece))
            t = t2
            i += step
    _write_short_ass(ass_path, chunks)

    ass_esc = ass_path.resolve().as_posix().replace(":", "\\:").replace("'", "\\'")
    vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        f"ass='{ass_esc}'"
    )
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        f"{start_sec:.3f}",
        "-i",
        str(horizontal_path),
        "-t",
        f"{length:.3f}",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "26",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
    if proc.returncode != 0 or not out_path.is_file():
        # retry without ass (font issues)
        logger.warning("short with ass failed, retry without: %s", (proc.stderr or "")[-300:])
        vf2 = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920"
        )
        cmd[cmd.index("-vf") + 1] = vf2
        proc2 = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
        if proc2.returncode != 0 or not out_path.is_file():
            raise RuntimeError(f"short render failed: {(proc2.stderr or '')[-500:]}")
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
            caption=cap,
            work_dir=work_dir,
        )
        clips.append(
            ShortClip(index=i, path=out, start_sec=start, end_sec=end, caption=cap)
        )
    return clips
