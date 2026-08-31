"""Рендер 16:9 (1920×1080) полной молитвы и 9:16 (1080×1920) шортсов."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

FULL_W = 1920
FULL_H = 1080
SHORT_W = 1080
SHORT_H = 1920
_ENCODE_PRESET = "medium"
_ENCODE_CRF = "20"

# Шрифты: display-семейства (есть на сервере), не Arial/DejaVu.
_FONT_THEME = "Noto Serif Display"
_FONT_CAPTION = "Noto Sans Display"
# ASS &HAABBGGRR — тёплый крем + мягкий уголь, без «кислотного» белого 90-х.
_COL_THEME = "&H00D8E8FF"  # тёплый ivory
_COL_CAPTION = "&H00F2F6FF"  # мягкий белый
_COL_OUTLINE = "&H40101820"  # полупрозрачный тёмный
_COL_SHADOW = "&H6E000000"

_STOPWORDS = {
    "и",
    "в",
    "во",
    "на",
    "с",
    "со",
    "о",
    "об",
    "а",
    "но",
    "не",
    "ни",
    "что",
    "это",
    "как",
    "же",
    "ли",
    "бы",
    "то",
    "к",
    "ко",
    "у",
    "за",
    "от",
    "по",
    "из",
    "для",
    "я",
    "ты",
    "мы",
    "вы",
    "он",
    "она",
    "они",
    "мне",
    "меня",
    "тебя",
    "нас",
    "вас",
    "его",
    "её",
    "их",
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "of",
    "in",
    "on",
    "for",
    "is",
    "are",
    "be",
    "i",
    "you",
    "we",
    "my",
    "your",
}


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


def format_prayer_theme_label(
    trend: str,
    brief: str = "",
    *,
    lang: str = "ru",
    max_len: int = 56,
    short: bool = False,
) -> str:
    """Постоянная подпись поверх ролика: коротко «Молитва: …»."""
    lang_l = (lang or "ru").lower()
    brief = re.sub(r"\s+", " ", (brief or "").strip())
    trend = re.sub(r"\s+", " ", (trend or "").strip())
    overlay_max = 38 if short else max_len

    def _clip(s: str, limit: int = overlay_max) -> str:
        s = s.strip(" .,—–-")
        if len(s) <= limit:
            return s
        cut = s[: limit - 1].rsplit(" ", 1)[0].strip()
        return (cut or s[: limit - 1]).rstrip(".,") + "…"

    if lang_l == "en":
        t = re.sub(r"^(a\s+)?prayer\s+(for|about|of)\s+", "", trend, flags=re.I).strip()
        return _clip(f"Prayer: {t}" if t else "Prayer")

    t = re.sub(
        r"^молитва\s+(о|об|про|за|:)\s*",
        "",
        trend,
        flags=re.I,
    ).strip(" :.—–-")
    if not t:
        return "Молитва"
    # Без склонения тренда — двоеточие читается чище, чем «о/об + именительный».
    if short:
        return _clip(f"Молитва: {t}")
    if brief.lower().startswith("молитва"):
        first = re.split(r"[.!?]", brief, 1)[0].strip()
        if 8 <= len(first) <= overlay_max:
            return _clip(first)
    return _clip(f"Молитва: {t}")


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


def _is_keyword(word: str) -> bool:
    w = re.sub(r"[^\w]+", "", (word or ""), flags=re.U).casefold().replace("ё", "е")
    if len(w) < 4:
        return False
    return w not in _STOPWORDS


def _kinetic_caption_text(text: str) -> str:
    """Крупные слова + лёгкий scale-pop на ключевых."""
    words = (text or "").split()
    if not words:
        return ""
    parts: List[str] = []
    for w in words:
        esc = _ass_escape_text(w)
        if _is_keyword(w):
            parts.append(
                r"{\fscx118\fscy118\t(0,220,\fscx100\fscy100)}" + esc
            )
        else:
            parts.append(esc)
    return " ".join(parts)


def _write_video_ass(
    path: Path,
    *,
    play_w: int,
    play_h: int,
    duration_sec: float,
    theme_label: str = "",
    chunks: Sequence[Tuple[float, float, str]] = (),
    vertical: bool = True,
    kinetic: bool = True,
    hook_question: str = "",
    hook_sec: float = 2.0,
) -> None:
    """
    ASS с тремя слоями:
    - Hook — крупный вопрос в первые ~2 с (центр)
    - Theme — постоянная подпись «Молитва о…» сверху
    - Caption — кинетическая строка молитвы крупно снизу
    """
    if vertical:
        theme_size, theme_margin_v = 58, 92
        theme_wrap = 16
        cap_size, cap_margin_v, wrap_chars = 68, 260, 22
        hook_size, wrap_hook = 72, 18
        margin_lr = 56
    else:
        theme_size, theme_margin_v = 42, 48
        theme_wrap = 22
        cap_size, cap_margin_v, wrap_chars = 58, 72, 36
        hook_size, wrap_hook = 78, 28
        margin_lr = 64

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_w}
PlayResY: {play_h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Theme,{_FONT_THEME},{theme_size},{_COL_THEME},&H000000FF,{_COL_OUTLINE},{_COL_SHADOW},1,0,0,0,100,100,1.2,0,1,3.2,1.4,8,{margin_lr},{margin_lr},{theme_margin_v},1
Style: Caption,{_FONT_CAPTION},{cap_size},{_COL_CAPTION},&H000000FF,{_COL_OUTLINE},{_COL_SHADOW},0,0,0,0,100,100,0.8,0,1,3.2,1.4,2,{margin_lr},{margin_lr},{cap_margin_v},1
Style: Hook,{_FONT_THEME},{hook_size},{_COL_THEME},&H000000FF,{_COL_OUTLINE},{_COL_SHADOW},0,0,0,0,100,100,1.0,0,1,4.0,2.0,5,{margin_lr},{margin_lr},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    dur = max(1.0, float(duration_sec))
    hook = (hook_question or "").strip()
    h_sec = max(1.2, min(3.0, float(hook_sec)))
    if hook:
        wrapped_hook = _ass_line_break(_wrap_line(hook, max_chars=wrap_hook))
        if wrapped_hook:
            lines.append(
                f"Dialogue: 2,0:00:00.00,{_sec_to_ass_time(h_sec)},"
                f"Hook,,0,0,0,,{{\\fad(120,350)\\blur0.6\\fscx108\\fscy108"
                f"\\t(0,280,\\fscx100\\fscy100)}}{wrapped_hook}"
            )
    theme = (theme_label or "").strip()
    if theme:
        wrapped_theme = _ass_line_break(_wrap_line(theme, max_chars=theme_wrap))
        if wrapped_theme:
            # тема появляется после hook, чтобы не конкурировать
            theme_start = h_sec if hook else 0.0
            lines.append(
                f"Dialogue: 1,{_sec_to_ass_time(theme_start)},{_sec_to_ass_time(dur)},"
                f"Theme,,0,0,0,,{{\\fad(400,0)\\blur0.4}}{wrapped_theme}"
            )
    for start, end, text in chunks:
        if end <= start or not (text or "").strip():
            continue
        # не перекрывать hook крупными субтитрами
        if hook and start < h_sec:
            start = h_sec
            if end <= start:
                continue
        if kinetic:
            wrapped_lines = _wrap_line(text, max_chars=wrap_chars)
            body = "\\N".join(_kinetic_caption_text(ln) for ln in wrapped_lines if ln.strip())
        else:
            body = _ass_line_break(_wrap_line(text, max_chars=wrap_chars))
        if not body:
            continue
        lines.append(
            f"Dialogue: 0,{_sec_to_ass_time(start)},{_sec_to_ass_time(end)},"
            f"Caption,,0,0,0,,{{\\fad(60,90)}}{body}"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_short_ass(
    path: Path,
    chunks: Sequence[Tuple[float, float, str]],
    *,
    theme_label: str = "",
    duration_sec: float = 0.0,
) -> None:
    """Совместимость: вертикальный ASS (Shorts)."""
    dur = duration_sec
    if dur <= 0 and chunks:
        dur = max(end for _s, end, _t in chunks)
    _write_video_ass(
        path,
        play_w=SHORT_W,
        play_h=SHORT_H,
        duration_sec=max(1.0, dur),
        theme_label=theme_label,
        chunks=chunks,
        vertical=True,
    )


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
    theme_label: str = "",
    work_dir: Optional[Path] = None,
    prayer_text: str = "",
    word_timings: Optional[Sequence[Tuple[float, float, str]]] = None,
    hook_question: str = "",
) -> Path:
    """16:9 Full HD + hook 2с + тема + кинетические субтитры по таймкодам TTS."""
    ffmpeg = _ffmpeg()
    dur = max(10.0, float(duration_sec))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wd = work_dir or out_path.parent
    wd.mkdir(parents=True, exist_ok=True)

    vf_base = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps=25,format=yuv420p"
    )

    chunks: List[Tuple[float, float, str]] = []
    if word_timings:
        from youtube_prayer.audio_pipeline import cue_chunks_from_words

        chunks = list(cue_chunks_from_words(word_timings, words_per_cue=5))
    elif prayer_text.strip():
        offset = _env_float("YT_PRAYER_SUBTITLE_OFFSET_SEC", -0.35)
        chunks = subtitle_chunks_full(
            prayer_text, duration_sec=dur, words_per_cue=5, offset_sec=offset
        )

    theme = (theme_label or "").strip()
    hook = (hook_question or "").strip()
    hook_sec = _env_float("YT_PRAYER_HOOK_SEC", 2.0)
    ass_path: Optional[Path] = None
    if theme or chunks or hook:
        ass_path = wd / f"{out_path.stem}_subs.ass"
        _write_video_ass(
            ass_path,
            play_w=width,
            play_h=height,
            duration_sec=dur,
            theme_label=theme,
            chunks=chunks,
            vertical=False,
            kinetic=True,
            hook_question=hook,
            hook_sec=hook_sec,
        )
        ass_esc = ass_path.resolve().as_posix().replace("\\", "/").replace(":", "\\:")
        vf = f"{vf_base},ass='{ass_esc}'"
    else:
        vf = vf_base

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
        vf,
        "-c:v",
        "libx264",
        "-preset",
        _ENCODE_PRESET,
        "-crf",
        _ENCODE_CRF,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-shortest",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1200, check=False)
    if proc.returncode != 0 or not out_path.is_file():
        if ass_path is not None:
            logger.warning(
                "horizontal with ass failed, retry plain: %s",
                (proc.stderr or "")[-400:],
            )
            cmd[cmd.index("-vf") + 1] = vf_base
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=1200, check=False
            )
        if proc.returncode != 0 or not out_path.is_file():
            raise RuntimeError(f"horizontal render failed: {(proc.stderr or '')[-600:]}")
    logger.info(
        "horizontal ok %s %sx%s bytes=%s theme=%r cues=%s",
        out_path.name,
        width,
        height,
        out_path.stat().st_size,
        theme[:40] if theme else "",
        len(chunks),
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
    theme_label: str = "",
) -> Path:
    """9:16 = 1080×1920, субтитры по словам окна аудио + тема сверху."""
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
    _write_short_ass(
        ass_path,
        chunks,
        theme_label=theme_label,
        duration_sec=length,
    )

    ass_esc = ass_path.resolve().as_posix().replace("\\", "/").replace(":", "\\:")
    vf = (
        f"scale={SHORT_W}:{SHORT_H}:force_original_aspect_ratio=increase,"
        f"crop={SHORT_W}:{SHORT_H},setsar=1,"
        f"ass='{ass_esc}'"
    )
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
        _ENCODE_PRESET,
        "-crf",
        _ENCODE_CRF,
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
    theme_label: str = "",
    word_timings: Optional[Sequence[Tuple[float, float, str]]] = None,
    hook_question: str = "",
) -> Path:
    """9:16: b-roll + аудио + hook + кинетические субтитры по таймкодам TTS."""
    ffmpeg = _ffmpeg()
    dur = max(10.0, float(duration_sec))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path = work_dir / f"{out_path.stem}.ass"
    if word_timings:
        from youtube_prayer.audio_pipeline import cue_chunks_from_words

        chunks = list(cue_chunks_from_words(word_timings, words_per_cue=4))
    else:
        offset = _env_float("YT_PRAYER_SUBTITLE_OFFSET_SEC", -0.35)
        chunks = subtitle_chunks_full(
            prayer_text,
            duration_sec=dur,
            words_per_cue=4,
            offset_sec=offset,
        )
    hook_sec = _env_float("YT_PRAYER_HOOK_SEC", 2.0)
    _write_video_ass(
        ass_path,
        play_w=width,
        play_h=height,
        duration_sec=dur,
        theme_label=theme_label,
        chunks=chunks,
        vertical=True,
        kinetic=True,
        hook_question=(hook_question or "").strip(),
        hook_sec=hook_sec,
    )
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
        _ENCODE_PRESET,
        "-crf",
        _ENCODE_CRF,
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
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1200, check=False)
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
        proc2 = subprocess.run(cmd, capture_output=True, text=True, timeout=1200, check=False)
        if proc2.returncode != 0 or not out_path.is_file():
            raise RuntimeError(
                f"vertical render failed: {(proc2.stderr or '')[-600:]}"
            )
    logger.info(
        "vertical ok %s %sx%s bytes=%s theme=%r cues=%s",
        out_path.name,
        width,
        height,
        out_path.stat().st_size,
        (theme_label or "")[:40],
        len(chunks),
    )
    return out_path


def render_all_shorts(
    *,
    horizontal_path: Path,
    prayer_text: str,
    duration_sec: float,
    work_dir: Path,
    theme_label: str = "",
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
            theme_label=theme_label,
        )
        clips.append(
            ShortClip(index=i, path=out, start_sec=start, end_sec=end, caption=cap)
        )
    return clips
