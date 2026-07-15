"""Типы данных RAG-слоя без зависимостей от Telegram/БД."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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
    "private_source_link": "приватная ссылка",
    "role": "роль",
    "voice_source": "голос",
}


def format_retrieval_sections(
    expert_block: str,
    testimonial_block: str,
) -> str:
    """Склейка блоков для промпта с явным разделением эксперт / клиент."""
    parts: List[str] = []
    ex = (expert_block or "").strip()
    te = (testimonial_block or "").strip()
    if ex:
        parts.append("=== Материалы эксперта (стиль, мысли, структура — опирайся на них) ===\n")
        parts.append(ex)
    if te:
        parts.append(
            "\n\n=== Отзывы клиентов (только цитаты и доказательства; НЕ копируй их тон как голос эксперта) ===\n"
        )
        parts.append(te)
    if not parts:
        return "(фрагменты из базы не найдены — опирайся на диалог)"
    return "\n".join(parts)


def format_retrieval_line(
    meta: Dict[str, Any],
    chunk_text: str,
) -> str:
    """Формат строки для промпта со всеми метаданными чанка.

    Выводит пары «ключ: значение» в заголовке, затем текст.
    """
    parts: List[str] = []
    cat = str(meta.get("content_category") or "").strip().lower()
    if cat == "testimonial":
        parts.append("⚠ отзыв клиента")

    for key in (
        "source", "product", "content_type", "content_category",
        "topic_title", "tags", "date", "role", "voice_source",
        "public_source_link", "private_source_link",
    ):
        val = str(meta.get(key) or "").strip()
        if not val:
            continue
        label = _META_KEY_LABELS.get(key, key)
        parts.append(f"{label}: {val}")

    for key, val in meta.items():
        if key in _META_SKIP_KEYS or key in _META_KEY_LABELS:
            continue
        sv = str(val).strip() if val is not None else ""
        if sv:
            parts.append(f"{key}: {sv}")

    header = " | ".join(parts) if parts else "unknown"
    return f"[{header}]\n{chunk_text}"
