"""Семантический поиск по коллекции expert_materials → текст для промпта."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

import chromadb

from rag.types import format_retrieval_line
from rag.vector_store import VectorStoreService

logger = logging.getLogger(__name__)

_CHROMA_WHERE_RETRY_MARKERS = ("finding id", "error executing plan")


def _is_chroma_where_index_error(exc: BaseException) -> bool:
    """Chroma 1.x Rust: HNSW/WAL рассинхрон при ``where=`` — типично «Error finding id»."""
    msg = str(exc).lower()
    return any(m in msg for m in _CHROMA_WHERE_RETRY_MARKERS)


def _run_expert_query(collection, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    where = kwargs.get("where")
    try:
        return collection.query(**kwargs)
    except (chromadb.errors.InternalError, chromadb.errors.ChromaError) as e:
        if where and _is_chroma_where_index_error(e):
            logger.warning(
                "Chroma where-filter failed (%s), retry without filter: %s",
                where,
                e,
            )
            kwargs_plain = {k: v for k, v in kwargs.items() if k != "where"}
            return collection.query(**kwargs_plain)
        raise


class ExpertRetriever:
    """Вызовы Chroma синхронные; публичные ``async`` методы гоняют их в thread pool."""

    def __init__(self, store: VectorStoreService):
        self._store = store

    def retrieve_context(
        self,
        query: str,
        *,
        top_k: int = 5,
        where: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Аналог ТЗ: ``collection.query`` + склейка блоков ``[source - tags]: chunk``.
        ``where`` — фильтр Chroma (metadata), или None.
        """
        q = (query or "").strip()
        if not q:
            return ""

        try:
            kwargs: Dict[str, Any] = {
                "query_texts": [q],
                "n_results": max(1, top_k),
            }
            if where:
                kwargs["where"] = where

            raw = _run_expert_query(self._store.expert_collection, kwargs)
        except Exception as e:
            logger.error("expert collection query failed: %s", e, exc_info=True)
            return ""

        return self._format_query_results(raw)

    async def retrieve_context_async(
        self,
        query: str,
        *,
        top_k: int = 5,
        where: Optional[Dict[str, Any]] = None,
    ) -> str:
        return await asyncio.to_thread(
            self.retrieve_context, query, top_k=top_k, where=where
        )

    def _query_raw(
        self,
        query: str,
        *,
        top_k: int,
        where: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        q = (query or "").strip()
        if not q:
            return {}
        kwargs: Dict[str, Any] = {
            "query_texts": [q],
            "n_results": max(1, top_k),
        }
        if where:
            kwargs["where"] = where
        return _run_expert_query(self._store.expert_collection, kwargs)

    def retrieve_merged_for_task(
        self,
        query: str,
        *,
        product: str,
        content_type: str,
        n_filtered: int = 5,
        n_broad: int = 3,
        max_chunks: int = 6,
    ) -> str:
        """
        Два запроса в expert_materials: с фильтром (product ИЛИ content_type) и без;
        склейка без дубликатов по id, порядок — сначала целевые, затем общие, обрезка.
        """
        q = (query or "").strip()
        if not q:
            return ""

        prod = (product or "").strip()
        ctype = (content_type or "").strip()
        seen: set[str] = set()
        merged_docs: List[str] = []
        merged_meta: List[Dict[str, Any]] = []

        def _ingest(raw: Dict[str, Any]) -> None:
            if not raw:
                return
            ids_list = raw.get("ids") or []
            docs_list = raw.get("documents") or []
            meta_list = raw.get("metadatas") or []
            if not ids_list or not ids_list[0]:
                return
            for cid, doc, meta in zip(ids_list[0], docs_list[0], meta_list[0]):
                if not cid or cid in seen:
                    continue
                if not doc or not str(doc).strip():
                    continue
                seen.add(cid)
                merged_docs.append(str(doc).strip())
                merged_meta.append(meta or {})
                if len(merged_docs) >= max_chunks:
                    return

        try:
            if prod and ctype:
                w = {"$or": [{"product": prod}, {"content_type": ctype}]}
                raw_f = self._query_raw(q, top_k=n_filtered, where=w)
                _ingest(raw_f)
            raw_b = self._query_raw(q, top_k=n_broad, where=None)
            _ingest(raw_b)
        except Exception as e:
            logger.error("retrieve_merged_for_task failed: %s", e, exc_info=True)
            return ""

        lines: List[str] = []
        for doc, meta in zip(merged_docs, merged_meta):
            lines.append(format_retrieval_line(meta or {}, doc))

        return "\n\n".join(lines)

    async def retrieve_merged_for_task_async(
        self,
        query: str,
        *,
        product: str,
        content_type: str,
        n_filtered: int = 5,
        n_broad: int = 3,
        max_chunks: int = 6,
    ) -> str:
        return await asyncio.to_thread(
            self.retrieve_merged_for_task,
            query,
            product=product,
            content_type=content_type,
            n_filtered=n_filtered,
            n_broad=n_broad,
            max_chunks=max_chunks,
        )

    def distinct_expert_metadata_values(
        self,
        field: str,
        *,
        max_scan: int = 50_000,
        page_size: int = 2_000,
    ) -> List[str]:
        """
        Уникальные значения поля metadata в коллекции expert_materials
        (например ``content_type``, ``product``) — для кнопок /new.

        Читает чанки страницами; при большой базе ограничение ``max_scan`` защищает от долгого цикла.
        """
        f = (field or "").strip()
        if not f:
            return []

        coll = self._store.expert_collection
        seen: set[str] = set()
        offset = 0
        while offset < max_scan:
            try:
                raw = coll.get(
                    include=["metadatas"],
                    limit=page_size,
                    offset=offset,
                )
            except Exception as e:
                logger.error("Chroma get distinct %s: %s", f, e, exc_info=True)
                break
            metas = raw.get("metadatas") or []
            if not metas:
                break
            for m in metas:
                if not m:
                    continue
                v = m.get(f)
                if v is None:
                    continue
                s = str(v).strip()
                if s:
                    seen.add(s)
            offset += len(metas)
            if len(metas) < page_size:
                break

        return sorted(seen)

    async def distinct_expert_metadata_values_async(
        self,
        field: str,
        *,
        max_scan: int = 50_000,
        page_size: int = 2_000,
    ) -> List[str]:
        return await asyncio.to_thread(
            self.distinct_expert_metadata_values,
            field,
            max_scan=max_scan,
            page_size=page_size,
        )

    def _format_query_results(self, raw: Dict[str, Any]) -> str:
        ids_list = raw.get("ids") or []
        docs_list = raw.get("documents") or []
        meta_list = raw.get("metadatas") or []

        if not ids_list or not ids_list[0]:
            return ""

        lines: List[str] = []
        for doc, meta in zip(docs_list[0], meta_list[0]):
            if not doc:
                continue
            lines.append(format_retrieval_line(meta or {}, doc.strip()))

        return "\n\n".join(lines)
