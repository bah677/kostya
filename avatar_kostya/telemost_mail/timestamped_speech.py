"""Реплики эксперта с таймкодами из TXT Телемоста."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Sequence

from telemost_mail.transcript import _name_matches

_TS_RE = re.compile(
    r"^\[(\d{1,2}):(\d{2}):(\d{2})\]\s*(.*)$"
)
_SPEAKER_HEADER_RE = re.compile(r"^([^:\[\n]{2,120}?)\s*:\s*$")


@dataclass(frozen=True)
class SpeechSegment:
    start_sec: float
    text: str
    end_sec: float
    speaker: str = ""

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)


def _hms_to_sec(h: str, m: str, s: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s)


def parse_speech_segments(
    transcript: str,
    speaker_names: Sequence[str],
    *,
    all_speakers: bool = False,
) -> List[SpeechSegment]:
    """Таймкоды для нарезки.

    all_speakers=False — только речь эксперта (эфир, вопрос/ответ).
    all_speakers=True — все спикеры (покаяние: Костя + участник).
    """
    lines = (transcript or "").splitlines()
    aliases = list(speaker_names)
    raw: List[tuple[float, str, str]] = []
    current_speaker: str | None = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if _SPEAKER_HEADER_RE.match(line):
            current_speaker = _SPEAKER_HEADER_RE.match(line).group(1).strip()
            continue
        m = _TS_RE.match(line)
        if not m or not current_speaker:
            continue
        if not all_speakers and not _name_matches(current_speaker, aliases):
            continue
        sec = _hms_to_sec(m.group(1), m.group(2), m.group(3))
        text = (m.group(4) or "").strip()
        if text:
            raw.append((sec, text, current_speaker))

    if not raw:
        return []

    out: List[SpeechSegment] = []
    for i, (start, text, speaker) in enumerate(raw):
        if i + 1 < len(raw):
            end = raw[i + 1][0]
        else:
            end = start + max(4.0, min(12.0, len(text) / 14.0))
        out.append(
            SpeechSegment(
                start_sec=start,
                text=text,
                end_sec=end,
                speaker=speaker,
            )
        )
    return out


def parse_expert_segments(
    transcript: str,
    speaker_names: Sequence[str],
) -> List[SpeechSegment]:
    return parse_speech_segments(transcript, speaker_names, all_speakers=False)


def merge_segments_window(
    segments: List[SpeechSegment],
    start_sec: float,
    end_sec: float,
) -> List[SpeechSegment]:
    return [
        s
        for s in segments
        if s.end_sec > start_sec and s.start_sec < end_sec
    ]


def format_expert_blocks_for_prompt(
    segments: Sequence[SpeechSegment],
    *,
    gap_sec: float = 12.0,
    limit_blocks: int = 90,
    skip_first_blocks: int = 0,
    max_text_per_block: int = 520,
) -> str:
    """Склеивает соседние реплики в блоки — удобнее искать целую мысль."""
    if not segments:
        return ""

    def _piece(s: SpeechSegment, last_speaker: str | None) -> tuple[str, str | None]:
        sp = (s.speaker or "").strip()
        if not sp:
            return s.text, last_speaker
        if last_speaker != sp:
            return f"{sp}: {s.text}", sp
        return s.text, sp

    blocks: List[tuple[float, float, str]] = []
    cur_start: float | None = None
    cur_end: float = 0.0
    texts: List[str] = []
    last_speaker: str | None = None
    for s in segments:
        piece, last_speaker = _piece(s, last_speaker)
        if cur_start is None:
            cur_start = s.start_sec
            cur_end = s.end_sec
            texts = [piece]
            continue
        if s.start_sec - cur_end <= gap_sec:
            texts.append(piece)
            cur_end = max(cur_end, s.end_sec)
        else:
            blocks.append((cur_start, cur_end, " ".join(texts)))
            cur_start = s.start_sec
            cur_end = s.end_sec
            last_speaker = None
            piece, last_speaker = _piece(s, last_speaker)
            texts = [piece]
    if cur_start is not None and texts:
        blocks.append((cur_start, cur_end, " ".join(texts)))

    pool = blocks[skip_first_blocks : skip_first_blocks + limit_blocks]
    lines: List[str] = []
    for a, b, text in pool:
        chunk = text.strip()
        if len(chunk) > max_text_per_block:
            chunk = chunk[: max_text_per_block - 1].rstrip() + "…"
        lines.append(f"[{a:.0f}–{b:.0f}s] {chunk}")
    return "\n\n".join(lines)
