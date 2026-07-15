"""Read-only structured queries к коллекциям Chroma."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import chromadb

from rag.retriever import _run_expert_query
from rag.types import format_retrieval_line

_MAX_TOP_K = 20


def clamp_top_k(top_k: int) -> int:
    return max(1, min(int(top_k or 5), _MAX_TOP_K))


def _chunks_from_raw(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not raw:
        return []
    ids_list = raw.get("ids") or []
    docs_list = raw.get("documents") or []
    meta_list = raw.get("metadatas") or []
    dist_list = raw.get("distances") or []
    if not ids_list or not ids_list[0]:
        return []

    ids = ids_list[0]
    docs = docs_list[0] if docs_list else []
    metas = meta_list[0] if meta_list else []
    dists = dist_list[0] if dist_list else []

    out: List[Dict[str, Any]] = []
    for i, cid in enumerate(ids):
        doc = docs[i] if i < len(docs) else ""
        meta = metas[i] if i < len(metas) else {}
        if not doc or not str(doc).strip():
            continue
        text = str(doc).strip()
        item: Dict[str, Any] = {
            "id": cid,
            "text": text,
            "metadata": dict(meta or {}),
            "formatted": format_retrieval_line(meta or {}, text),
        }
        if i < len(dists) and dists[i] is not None:
            item["distance"] = float(dists[i])
        out.append(item)
    return out


def query_expert_collection(
    collection,
    *,
    query: str,
    top_k: int = 5,
    where: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    q = (query or "").strip()
    if not q:
        return {"query": "", "chunks": [], "context_text": ""}

    k = clamp_top_k(top_k)
    kwargs: Dict[str, Any] = {
        "query_texts": [q],
        "n_results": k,
    }
    if where:
        kwargs["where"] = where

    raw = _run_expert_query(collection, kwargs)
    chunks = _chunks_from_raw(raw)
    context_text = "\n\n".join(c["formatted"] for c in chunks)
    return {
        "query": q,
        "top_k": k,
        "where": where,
        "chunks": chunks,
        "context_text": context_text,
    }


def query_golden_collection(
    collection,
    *,
    query: str,
    top_k: int = 3,
    where: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    q = (query or "").strip()
    if not q:
        return {"query": "", "examples": [], "few_shot_text": ""}

    k = clamp_top_k(top_k)
    kwargs: Dict[str, Any] = {
        "query_texts": [q],
        "n_results": k,
    }
    if where:
        kwargs["where"] = where

    try:
        raw = collection.query(**kwargs)
    except (chromadb.errors.InternalError, chromadb.errors.ChromaError):
        if where:
            raw = collection.query(query_texts=[q], n_results=k)
        else:
            raise

    chunks = _chunks_from_raw(raw)
    examples: List[Dict[str, Any]] = []
    blocks: List[str] = []
    for i, ch in enumerate(chunks, start=1):
        meta = ch.get("metadata") or {}
        ans = str(meta.get("answer") or "").strip()
        topic = str(meta.get("topic") or ch.get("text") or "").strip()
        examples.append(
            {
                "id": ch.get("id"),
                "topic": topic,
                "answer": ans,
                "metadata": meta,
                "distance": ch.get("distance"),
            }
        )
        blocks.append(f"Пример {i}\nТема: {topic}\nОтвет:\n{ans}".strip())

    return {
        "query": q,
        "top_k": k,
        "where": where,
        "examples": examples,
        "few_shot_text": "\n\n---\n\n".join(blocks),
    }
