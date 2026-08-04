"""Нормализация текста молитвы под ElevenLabs (как в biblia format_prayer_for_tts)."""

from __future__ import annotations

import re

_AMEN_STRESSED = "амИнь"
_AMEN_FLEX_RE = re.compile(
    r"(?iu)\bа[\u0300\u0301\u0341]?м[\u0300\u0301\u0341]?и[\u0300\u0301\u0341]?"
    r"н[\u0300\u0301\u0341]?ь\b"
)
_AMEN_EN_RE = re.compile(r"(?i)\bamen\.?\b")
_LONG_SENT_WORDS = 20
_PAIR_MAX_WORDS = 16
_BREATH_GAP_RE = re.compile(
    r"(?i)(?:,\s+|\s+(?:чтобы|ибо|потому что|когда|если|because|when|if|so that)\s+)"
)


def ensure_amen_stress(text: str) -> str:
    return _AMEN_FLEX_RE.sub(_AMEN_STRESSED, text or "")


def _word_count(s: str) -> int:
    return len([w for w in (s or "").split() if w])


def _insert_breath_in_long_sentence(sent: str) -> str:
    s = (sent or "").strip()
    if _word_count(s) < _LONG_SENT_WORDS:
        return s
    if " — " in s or " – " in s:
        return s
    mid = len(s) // 2
    best: int | None = None
    best_dist = 10**9
    for m in _BREATH_GAP_RE.finditer(s):
        if m.start() < 12 or m.end() > len(s) - 12:
            continue
        dist = abs(m.start() - mid)
        if dist < best_dist:
            best_dist = dist
            best = m.start()
    if best is None:
        return s
    left = s[:best].rstrip(" ,")
    right = s[best:].lstrip(" ,")
    if not left or not right:
        return s
    return f"{left} — {right}"


def _pack_prayer_paragraphs(sentences: list[str]) -> list[str]:
    paras: list[str] = []
    buf: list[str] = []
    buf_words = 0

    def flush() -> None:
        nonlocal buf, buf_words
        if not buf:
            return
        paras.append(" ".join(buf).strip())
        buf = []
        buf_words = 0

    for raw in sentences:
        s = _insert_breath_in_long_sentence(raw.strip())
        if not s:
            continue
        w = _word_count(s)
        if w >= _LONG_SENT_WORDS:
            flush()
            paras.append(s)
            continue
        if buf and (buf_words + w > _PAIR_MAX_WORDS or len(buf) >= 2):
            flush()
        buf.append(s)
        buf_words += w
        if len(buf) >= 2 or buf_words >= _PAIR_MAX_WORDS:
            flush()
    flush()
    return paras


def format_prayer_for_tts(text: str, *, lang: str = "ru") -> str:
    t = (text or "").strip()
    t = re.sub(r"^```(?:\w+)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    t = t.strip().strip('"').strip("«»")
    t = re.sub(r"\+(?=[аАеЕёЁиИоОуУыЫэЭюЮяЯaAeEiIoOuU])", "", t)
    t = re.sub(r"[\u0300\u0301\u0341]", "", t)
    t = t.replace("…", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)

    if "\n\n" in t:
        raw_parts = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    else:
        flat = re.sub(r"\n+", " ", t).strip()
        raw_parts = [flat] if flat else []

    sentences: list[str] = []
    for part in raw_parts:
        bits = re.split(r"(?<=[.!?])\s+", part)
        sentences.extend(b.strip() for b in bits if b.strip())

    amen = None
    if sentences:
        last = sentences[-1]
        if lang == "en":
            if _AMEN_EN_RE.search(last):
                amen = last if "Amen" in last else _AMEN_EN_RE.sub("Amen", last)
                sentences = sentences[:-1]
        elif _AMEN_FLEX_RE.search(last):
            amen = ensure_amen_stress(last)
            sentences = sentences[:-1]

    paras = _pack_prayer_paragraphs(sentences)
    out = "\n\n".join(paras)
    if amen:
        out = f"{out}\n\n{amen}".strip() if out else amen
    if lang == "en":
        return out
    return ensure_amen_stress(out) if out else ""


def prayer_text_looks_complete(text: str, *, lang: str = "ru") -> bool:
    t = (text or "").strip()
    if len(t) < 120:
        return False
    tail = t[-200:].casefold()
    if lang == "en":
        return "amen" in tail
    tail = tail.replace("і", "и").replace("i", "и")
    return "аминь" in tail


def strip_prayer_text(raw: str) -> str:
    t = (raw or "").strip()
    t = re.sub(r"^```(?:\w+)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    return t.strip().strip('"').strip("«»")
