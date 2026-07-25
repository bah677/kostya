"""Опциональный RAG-контекст (read-only Chroma avatar), best-effort."""

from __future__ import annotations

import logging
from typing import Optional

from config import Config

logger = logging.getLogger(__name__)


def try_rag_snippet(cfg: Config, query: str, *, n: int = 4) -> str:
    if not cfg.RAG_ENABLED or not cfg.OPENAI_API_KEY or not cfg.RAG_CHROMA_PERSIST_DIR:
        return ""
    try:
        # Prefer club's rag package if importable from monorepo
        import sys
        from pathlib import Path

        club_root = Path(cfg.GITHUB_BIBLIA_PATH).resolve().parent / "club"
        if club_root.is_dir() and str(club_root) not in sys.path:
            sys.path.insert(0, str(club_root))
        from rag.runtime import build_rag_stack
        from rag.settings import RAGSettings

        settings = RAGSettings(
            openai_api_key=cfg.OPENAI_API_KEY,
            persist_directory=cfg.RAG_CHROMA_PERSIST_DIR,
            expert_collection_name=cfg.RAG_EXPERT_COLLECTION,
            golden_collection_name=cfg.RAG_GOLDEN_COLLECTION,
            embedding_model=cfg.RAG_EMBEDDING_MODEL,
            chunk_size_tokens=600,
            chunk_overlap_tokens=100,
            tiktoken_encoding="cl100k_base",
        )
        stack = build_rag_stack(settings)
        docs = stack.similarity_search_expert(query, k=n)  # type: ignore[attr-defined]
        parts = []
        for d in docs or []:
            text = getattr(d, "page_content", None) or getattr(d, "content", None) or str(d)
            parts.append(str(text)[:500])
        return "\n---\n".join(parts)[:3000]
    except Exception as e:
        logger.info("RAG snippet skipped: %s", e)
        return ""
