"""
Подключение к **готовому** prod-RAG проекта avatar_kostya (только чтение Chroma).

Клуб не индексирует и не пишет в базу — только semantic search + golden few-shot
для дополнения system prompt агента.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from rag.runtime import RagStack, build_rag_stack
from rag.settings import RAGSettings

if TYPE_CHECKING:
    from config import Config

logger = logging.getLogger(__name__)

# Параметры чанкинга нужны классу настроек, но в клубе индексации нет — как в avatar_kostya.
_AVATAR_RAG_CHUNK_SIZE = 600
_AVATAR_RAG_CHUNK_OVERLAP = 100
_AVATAR_RAG_TIKTOKEN = "cl100k_base"


def try_build_rag_stack(cfg: "Config") -> Optional[RagStack]:
    """
    Read-only клиент к внешнему Chroma.

    Требует ``RAG_ENABLED=1``, ``OPENAI_API_KEY`` и ``RAG_CHROMA_PERSIST_DIR``
    (каталог ``chroma_data`` из avatar_kostya).
    """
    if not cfg.RAG_ENABLED:
        logger.info("RAG_DISABLED: внешний prod-RAG не подключаем")
        return None

    key = (cfg.OPENAI_API_KEY or "").strip()
    if not key:
        logger.warning("RAG_ENABLED без OPENAI_API_KEY — embeddings недоступны")
        return None

    persist = cfg.resolved_rag_chroma_persist_dir
    if not persist or not str(persist).strip():
        logger.error(
            "RAG_ENABLED, но RAG_CHROMA_PERSIST_DIR пуст — укажите путь к chroma_data avatar_kostya"
        )
        return None
    if not Path(persist).is_dir():
        logger.error(
            "RAG: каталог prod Chroma не найден: %s (проверьте RAG_CHROMA_PERSIST_DIR)",
            persist,
        )
        return None

    settings = RAGSettings(
        openai_api_key=key,
        persist_directory=str(persist),
        expert_collection_name=cfg.RAG_EXPERT_COLLECTION,
        golden_collection_name=cfg.RAG_GOLDEN_COLLECTION,
        embedding_model=cfg.RAG_EMBEDDING_MODEL,
        chunk_size_tokens=_AVATAR_RAG_CHUNK_SIZE,
        chunk_overlap_tokens=_AVATAR_RAG_CHUNK_OVERLAP,
        tiktoken_encoding=_AVATAR_RAG_TIKTOKEN,
    )
    stack = build_rag_stack(settings)
    ne, ng = stack.expert_count_golden_count()
    logger.info(
        "RAG read-only: prod Chroma %s (expert≈%s, golden≈%s)",
        persist,
        ne,
        ng,
    )
    return stack
