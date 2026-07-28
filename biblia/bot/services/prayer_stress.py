"""Сервис разбора и применения пользовательских ударений для TTS молитв."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List

_RU_VOWELS = "аеёиоуыэюя"
_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё\-]+")


@dataclass(frozen=True)
class PrayerStressWord:
    source_word: str
    base_word: str
    accented_word: str


def parse_prayer_stress_words(raw: str, *, limit: int = 20) -> tuple[List[PrayerStressWord], List[str]]:
    parts = re.split(r"[,;\n]+", raw or "")
    ok: List[PrayerStressWord] = []
    bad: List[str] = []
    seen: set[str] = set()

    for part in parts:
        token = (part or "").strip()
        if not token:
            continue
        if len(ok) >= limit:
            break
        word = token.replace(" ", "")
        if not re.fullmatch(r"[A-Za-zА-Яа-яЁё\-]+", word):
            bad.append(token)
            continue
        uppers = [ch for ch in word if ch.isupper()]
        if len(uppers) != 1:
            bad.append(token)
            continue
        upper = uppers[0].lower()
        if upper not in _RU_VOWELS and upper not in "aeiouy":
            bad.append(token)
            continue
        base = word.lower().replace("ё", "е")
        if base in seen:
            continue
        seen.add(base)
        ok.append(
            PrayerStressWord(
                source_word=word,
                base_word=base,
                accented_word=word,
            )
        )
    return ok, bad


def build_prayer_stress_sample_text(accented_word: str) -> str:
    return f"Господи, благослови нас словом {accented_word}."


def apply_prayer_stress_dictionary(text: str, dictionary: Dict[str, str]) -> str:
    if not text or not dictionary:
        return text or ""

    def _repl(match: re.Match[str]) -> str:
        token = match.group(0)
        base = token.lower().replace("ё", "е")
        return dictionary.get(base, token)

    return _TOKEN_RE.sub(_repl, text)


def dictionary_hits(text: str, dictionary: Dict[str, str]) -> List[str]:
    if not text or not dictionary:
        return []
    hits: List[str] = []
    seen: set[str] = set()
    for token in _TOKEN_RE.findall(text):
        base = token.lower().replace("ё", "е")
        if base in dictionary and base not in seen:
            seen.add(base)
            hits.append(base)
    return hits
