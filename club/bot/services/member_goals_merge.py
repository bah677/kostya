"""Слияние фрагментов stated_goals без перезаписи и дублей."""

from __future__ import annotations

import re

_MAX_STATED_GOALS = 2000
_WS_RE = re.compile(r"\s+")


def _normalize_for_dedup(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").lower().strip())


def _existing_parts(existing: str) -> list[str]:
    parts: list[str] = []
    for chunk in re.split(r"[\n•|]+", existing or ""):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    return parts


def merge_stated_goals_fragment(
    existing: str,
    fragment: str,
    *,
    max_len: int = _MAX_STATED_GOALS,
) -> tuple[str, bool]:
    """
    Дополняет цели новым фрагментом.

    Возвращает (итоговый текст, changed).
    Никогда не удаляет и не заменяет уже записанные цели.
    """
    frag = (fragment or "").strip()
    if not frag or len(frag) < 5:
        return (existing or "").strip(), False

    ex = (existing or "").strip()
    norm_frag = _normalize_for_dedup(frag)
    if not norm_frag:
        return ex, False

    if ex:
        norm_ex = _normalize_for_dedup(ex)
        if norm_frag in norm_ex:
            return ex, False
        for part in _existing_parts(ex):
            norm_part = _normalize_for_dedup(part)
            if norm_part == norm_frag or norm_frag in norm_part or norm_part in norm_frag:
                return ex, False

    merged = f"{ex}\n• {frag}" if ex else frag
    if len(merged) > max_len:
        trimmed = merged[:max_len]
        if "\n" in trimmed:
            trimmed = trimmed.rsplit("\n", 1)[0]
        merged = trimmed.rstrip()
    return merged, merged != ex
