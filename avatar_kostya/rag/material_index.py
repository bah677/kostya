"""
Индексация сырых материалов: чанкинг, нормализация metadata, запись в expert_materials.

Вызывается из группового индексера бота; сам по себе не знает про Telegram.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from typing import Any, Dict, List, Optional

from rag.chunking import chunk_text_by_tokens
from rag.settings import RAGSettings
from rag.types import normalize_chroma_metadata
from rag.vector_store import VectorStoreService

logger = logging.getLogger(__name__)


def dedupe_material_key(
    source: str, text: str, chunk_index: int, *, dedupe_salt: str = ""
) -> str:
    """Стабильный id чанка для дедупликации (хеш + источник + индекс + соль сообщения)."""
    head = (text or "")[:200]
    salt = (dedupe_salt or "").strip()
    h = hashlib.md5(f"{head}|{source}|{salt}".encode("utf-8")).hexdigest()[:16]
    return f"{h}:{source[:80]}:{chunk_index}"


class MaterialIndexService:
    def __init__(self, store: VectorStoreService):
        self._store = store

    @property
    def settings(self) -> RAGSettings:
        return self._store.settings

    def add_material_text(
        self,
        full_text: str,
        *,
        base_metadata: Optional[Dict[str, Any]] = None,
        source: str = "manual",
        dedupe_salt: str = "",
    ) -> tuple[int, List[str]]:
        """
        Чанкует текст и добавляет в expert_materials.

        Returns:
            (число добавленных чанков, список id).
        """
        base_metadata = dict(base_metadata or {})
        base_metadata.setdefault("source", source)

        chunks = chunk_text_by_tokens(
            full_text,
            chunk_size=self.settings.chunk_size_tokens,
            overlap=self.settings.chunk_overlap_tokens,
            encoding_name=self.settings.tiktoken_encoding,
        )
        if not chunks:
            return 0, []

        ids: List[str] = []
        documents: List[str] = []
        metadatas: List[Dict[str, Any]] = []

        for i, ch in enumerate(chunks):
            chunk_id = dedupe_material_key(source, ch, i, dedupe_salt=dedupe_salt)
            try:
                existing = self._store.expert_collection.get(ids=[chunk_id])
                if existing and existing.get("ids") and existing["ids"]:
                    logger.debug("skip duplicate chunk id=%s", chunk_id)
                    continue
            except Exception:
                pass

            meta = normalize_chroma_metadata({**base_metadata, "chunk_index": i})
            ids.append(chunk_id)
            documents.append(ch)
            metadatas.append(meta)

        if not ids:
            return 0, []

        try:
            self._store.expert_collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            )
            return len(ids), ids
        except Exception as e:
            logger.error("expert add failed: %s", e, exc_info=True)
            return 0, []

    async def add_material_text_async(
        self,
        full_text: str,
        *,
        base_metadata: Optional[Dict[str, Any]] = None,
        source: str = "manual",
        dedupe_salt: str = "",
    ) -> tuple[int, List[str]]:
        return await asyncio.to_thread(
            self.add_material_text,
            full_text,
            base_metadata=base_metadata,
            source=source,
            dedupe_salt=dedupe_salt,
        )

    def has_chunk_id(self, chunk_id: str) -> bool:
        try:
            r = self._store.expert_collection.get(ids=[chunk_id])
            return bool(r and r.get("ids"))
        except Exception:
            return False
