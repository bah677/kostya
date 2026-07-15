"""Коллекция golden_examples: few-shot по теме (эмбеддинг темы, текст ответа в metadata)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

from rag.types import normalize_chroma_metadata
from rag.vector_store import VectorStoreService

logger = logging.getLogger(__name__)


class GoldenExamplesStore:
    def __init__(self, store: VectorStoreService):
        self._store = store

    def add_example(
        self,
        topic: str,
        assistant_reply: str,
        *,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Сохраняет пару тема → ответ. В коллекции документ = тема (для поиска),
        metadata содержит полный ответ.
        """
        t = (topic or "").strip()
        a = (assistant_reply or "").strip()
        if not t or not a:
            return None

        cid = str(uuid.uuid4())
        meta: Dict[str, Any] = dict(extra_metadata or {})
        meta["topic"] = t[:2000]
        meta["answer"] = a[:16000]
        meta = normalize_chroma_metadata(meta)

        try:
            self._store.golden_collection.add(
                ids=[cid],
                documents=[t],
                metadatas=[meta],
            )
            return cid
        except Exception as e:
            logger.error("golden add failed: %s", e, exc_info=True)
            return None

    async def add_example_async(
        self,
        topic: str,
        assistant_reply: str,
        *,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        return await asyncio.to_thread(
            self.add_example,
            topic,
            assistant_reply,
            extra_metadata=extra_metadata,
        )

    def format_few_shot_block(
        self,
        query: str,
        *,
        top_k: int = 2,
    ) -> str:
        """Текст блока few-shot для подстановки в промпт."""
        q = (query or "").strip()
        if not q:
            return ""

        try:
            raw = self._store.golden_collection.query(
                query_texts=[q],
                n_results=max(1, top_k),
            )
        except Exception as e:
            logger.error("golden query failed: %s", e, exc_info=True)
            return ""

        docs_list = raw.get("documents") or []
        meta_list = raw.get("metadatas") or []
        if not docs_list or not docs_list[0]:
            return ""

        blocks: List[str] = []
        for i, (doc, meta) in enumerate(zip(docs_list[0], meta_list[0]), start=1):
            meta = meta or {}
            ans = meta.get("answer") or ""
            topic_saved = meta.get("topic") or doc or ""
            blocks.append(
                f"Пример {i}\nТема: {topic_saved}\nОтвет:\n{ans}".strip()
            )
        return "\n\n---\n\n".join(blocks)

    async def format_few_shot_block_async(
        self,
        query: str,
        *,
        top_k: int = 2,
    ) -> str:
        return await asyncio.to_thread(
            self.format_few_shot_block, query, top_k=top_k
        )

    def format_few_shot_block_filtered(
        self,
        query: str,
        *,
        product: str,
        content_type: str,
        top_k: int = 2,
    ) -> str:
        """Few-shot с фильтром по метаданным; при пустом результате — без фильтра."""
        q = (query or "").strip()
        if not q:
            return ""
        p = (product or "").strip()
        c = (content_type or "").strip()
        where = None
        if p and c:
            where = {"$or": [{"product": p}, {"content_type": c}]}
        try:
            kwargs: Dict[str, Any] = {
                "query_texts": [q],
                "n_results": max(1, top_k),
            }
            if where:
                kwargs["where"] = where
            raw = self._store.golden_collection.query(**kwargs)
        except Exception as e:
            logger.warning("golden query filtered failed: %s", e)
            return self.format_few_shot_block(query, top_k=top_k)

        docs_list = raw.get("documents") or []
        meta_list = raw.get("metadatas") or []
        if docs_list and docs_list[0]:
            return self._format_few_shot_from_raw(docs_list, meta_list)

        if where:
            return self.format_few_shot_block(query, top_k=top_k)
        return ""

    def _format_few_shot_from_raw(
        self,
        docs_list: List,
        meta_list: List,
    ) -> str:
        if not docs_list or not docs_list[0]:
            return ""
        blocks: List[str] = []
        for i, (doc, meta) in enumerate(zip(docs_list[0], meta_list[0]), start=1):
            meta = meta or {}
            ans = meta.get("answer") or ""
            topic_saved = meta.get("topic") or doc or ""
            blocks.append(
                f"Пример {i}\nТема: {topic_saved}\nОтвет:\n{ans}".strip()
            )
        return "\n\n---\n\n".join(blocks)

    async def format_few_shot_block_filtered_async(
        self,
        query: str,
        *,
        product: str,
        content_type: str,
        top_k: int = 2,
    ) -> str:
        return await asyncio.to_thread(
            self.format_few_shot_block_filtered,
            query,
            product=product,
            content_type=content_type,
            top_k=top_k,
        )
