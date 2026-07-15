"""Добавление фрагментов внешнего prod-RAG (avatar_kostya) к промпту менеджера клуба."""

from __future__ import annotations

from bot.texts.prompts.rag_augmentation import (
    GOLDEN_SECTION_HEADER,
    RAG_RULES,
    RAG_SECTION_EMPTY,
    RAG_SECTION_HEADER,
)


def augment_system_prompt_with_rag(
    base_system: str,
    *,
    retrieved_context: str,
    golden_block: str,
) -> str:
    rc = (retrieved_context or "").strip()
    gb = (golden_block or "").strip()
    if not rc and not gb:
        return base_system

    lines = [
        base_system,
        "",
        RAG_SECTION_HEADER,
        rc or RAG_SECTION_EMPTY,
    ]
    if gb:
        lines.extend(["", GOLDEN_SECTION_HEADER, gb])
    lines.extend(["", *RAG_RULES])
    return "\n".join(lines)
