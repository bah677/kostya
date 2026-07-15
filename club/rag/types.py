"""Типы данных RAG-слоя без зависимостей от Telegram/БД."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from rag.source_links import apply_classified_link_metadata


@dataclass
class ChunkRecord:
    """Один чанк для записи в коллекцию expert_materials."""

    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


def normalize_chroma_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
    """
    Chroma принимает в metadata только str | int | float | bool.
    Списки/прочее — JSON-строка или строка через запятую.
    """
    out: Dict[str, Any] = {}
    for k, v in meta.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, list):
            out[k] = ", ".join(str(x) for x in v)
        else:
            out[k] = str(v)
    return out


_META_SKIP_KEYS = frozenset({
    "chunk_index", "added_by",
})

_META_KEY_LABELS = {
    "source": "источник",
    "content_type": "тип",
    "content_category": "вид",
    "product": "продукт",
    "tags": "теги",
    "date": "дата",
    "topic_title": "топик",
    "public_source_link": "публичная ссылка",
    "role": "роль",
}

# Приватные и legacy-ссылки в промпт member-агента не попадают.
_META_PROMPT_SKIP_KEYS = frozenset({
    "private_source_link",
    "group_message_link",
})

def format_retrieval_line(
    meta: Dict[str, Any],
    chunk_text: str,
) -> str:
    """Формат строки для промпта: публичные метаданные + текст (без приватных ссылок)."""
    m = dict(meta or {})
    m, _ = apply_classified_link_metadata(m)

    parts: List[str] = []
    for key in (
        "source", "product", "content_type", "content_category",
        "topic_title", "tags", "date", "role", "public_source_link",
    ):
        val = str(m.get(key) or "").strip()
        if not val:
            continue
        label = _META_KEY_LABELS.get(key, key)
        parts.append(f"{label}: {val}")

    for key, val in m.items():
        if key in _META_SKIP_KEYS or key in _META_KEY_LABELS or key in _META_PROMPT_SKIP_KEYS:
            continue
        sv = str(val).strip() if val is not None else ""
        if sv:
            parts.append(f"{key}: {sv}")

    header = " | ".join(parts) if parts else "unknown"
    return f"[{header}]\n{chunk_text}"
