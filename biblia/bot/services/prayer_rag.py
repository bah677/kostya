"""Примеры структуры утренних молитв из Chroma RAG (avatar expert_materials)."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from pathlib import Path
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

_PRAYER_CONTENT_TYPES = ("Молитвы", "Молитва")
_SCORE_WORDS = (
    "отец",
    "господ",
    "боже",
    "аминь",
    "благода",
    "прошу",
    "исус",
    "христ",
    "дух",
    "любов",
)
_BAD_MARKERS = ("[не удалось", "не удалось скачать")


def _default_chroma_dir() -> Path:
    # Dev monorepo: avatar_kostya рядом с biblia.
    here = Path(__file__).resolve()
    candidate = here.parents[2] / "avatar_kostya" / "chroma_data"
    if candidate.is_dir():
        return candidate
    return Path("/home/appuser/dev/kostya/avatar_kostya/chroma_data")


def _normalize_query(text: str) -> List[str]:
    words = re.findall(r"[а-яёa-z0-9]{3,}", (text or "").lower(), flags=re.I)
    # убрать слишком общие
    stop = {"это", "что", "как", "для", "меня", "мне", "чтобы", "есть", "был", "была"}
    return [w for w in words if w not in stop][:40]


def _score_doc(doc: str, query_words: Sequence[str]) -> int:
    low = (doc or "").lower()
    if len(low) < 350:
        return -1
    if any(b in low for b in _BAD_MARKERS):
        return -1
    score = sum(1 for w in _SCORE_WORDS if w in low)
    if query_words:
        score += sum(2 for w in query_words if w in low)
    # предпочтение «утренних» источников
    if "утрен" in low:
        score += 1
    return score


def _format_examples(chunks: List[str], *, max_chars: int = 3500) -> str:
    parts: List[str] = []
    used = 0
    for i, chunk in enumerate(chunks, 1):
        body = re.sub(r"\s+\n", "\n", (chunk or "").strip())
        body = re.sub(r"\n{3,}", "\n\n", body)
        if len(body) > 1400:
            body = body[:1400].rsplit(" ", 1)[0] + "…"
        block = f"--- пример {i} ---\n{body}"
        if used + len(block) > max_chars and parts:
            break
        parts.append(block)
        used += len(block) + 2
    return "\n\n".join(parts)


def _fetch_sync(
    *,
    chroma_dir: Path,
    collection: str,
    query: str,
    top_k: int,
    fetch_limit: int,
) -> str:
    try:
        from chromadb import PersistentClient
    except ImportError:
        logger.warning("prayer RAG: chromadb не установлен в venv biblia — примеры пропущены")
        return ""

    if not chroma_dir.is_dir():
        logger.warning("prayer RAG: нет chroma dir %s", chroma_dir)
        return ""

    try:
        client = PersistentClient(path=str(chroma_dir))
        col = client.get_collection(collection)
        raw = col.get(
            where={"content_type": {"$in": list(_PRAYER_CONTENT_TYPES)}},
            include=["documents", "metadatas"],
            limit=max(50, min(2000, int(fetch_limit))),
        )
    except Exception as e:
        logger.error("prayer RAG get failed: %s", e, exc_info=True)
        return ""

    docs = raw.get("documents") or []
    metas = raw.get("metadatas") or []
    qwords = _normalize_query(query)
    scored: List[tuple[int, str]] = []
    for doc, meta in zip(docs, metas):
        text = (doc or "").strip()
        score = _score_doc(text, qwords)
        if score < 2:
            continue
        src = ""
        if isinstance(meta, dict):
            src = str(meta.get("source") or meta.get("topic_title") or "")
        if "утрен" in src.lower():
            score += 2
        scored.append((score, text))

    if not scored:
        logger.info("prayer RAG: подходящих чанков не найдено")
        return ""

    scored.sort(key=lambda x: x[0], reverse=True)
    # берём из топа с лёгкой рандомизацией, чтобы A/B не залипал на одном примере
    pool = scored[: max(top_k * 6, 12)]
    random.shuffle(pool)
    picked = [t for _, t in pool[: max(1, top_k)]]
    return _format_examples(picked)


async def fetch_prayer_style_examples(
    query: str,
    *,
    top_k: Optional[int] = None,
) -> str:
    """Возвращает текст примеров структуры/стиля или пустую строку."""
    from config import config

    if not bool(getattr(config, "PRAYER_RAG_ENABLED", True)):
        return ""

    chroma_raw = (getattr(config, "PRAYER_RAG_CHROMA_DIR", None) or "").strip()
    chroma_dir = Path(chroma_raw) if chroma_raw else _default_chroma_dir()
    collection = (getattr(config, "PRAYER_RAG_COLLECTION", None) or "expert_materials").strip()
    k = int(top_k if top_k is not None else getattr(config, "PRAYER_RAG_TOP_K", 2) or 2)
    fetch_limit = int(getattr(config, "PRAYER_RAG_FETCH_LIMIT", 800) or 800)

    return await asyncio.to_thread(
        _fetch_sync,
        chroma_dir=chroma_dir,
        collection=collection,
        query=query or "",
        top_k=max(1, min(5, k)),
        fetch_limit=fetch_limit,
    )


def build_compose_user_content(turns_block: str, *, style_examples: str = "") -> str:
    parts = [turns_block.strip()]
    examples = (style_examples or "").strip()
    if examples:
        parts.append(
            "ПРИМЕР СТРУКТУРЫ И СТИЛЯ (только ритм/манера; содержание бери из диалога выше; "
            "не копируй утренние формулировки вроде «этим утром / начало дня», "
            "если пользователь сам об утре не говорил):\n"
            + examples
        )
    return "\n\n".join(parts)
