"""
Универсальный пайплайн поиска по внешнему prod-RAG (avatar_kostya, только чтение).

Каждое сообщение в ЛС (если RAG включён и не sensitive):
1. Текущие дата/время МСК → в промпт агента.
2. Эвристики: относительные даты, ссылки t.me — подсказка планировщику.
3. Обязательный короткий вызов LLM → JSON-план запросов к Chroma.
4. Поиск: точный metadata-scan + параллельная семантика по всем запросам плана → слияние.
5. Golden few-shot по нескольким запросам.
6. Основной ответ агента с объединённым контекстом.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from zoneinfo import ZoneInfo

from bot.services.agent_datetime_context import format_datetime_context, now_msk
from rag.source_links import metadata_link_fields_for_scan
from rag.types import format_retrieval_line
from storage.db.llm_token_normalize import extract_token_counts_and_extras

if TYPE_CHECKING:
    from storage.database import Database
    from openai import AsyncOpenAI
    from rag.runtime import RagStack

logger = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")
_RAG_QUERY_SEMAPHORE = asyncio.Semaphore(4)

_TME_LINK_RE = re.compile(
    r"https?://t\.me/c/(\d+)/(?:\d+/)?(\d+)",
    re.IGNORECASE,
)
_REL_YESTERDAY = re.compile(r"\bвчера\b", re.IGNORECASE)
_REL_DAY_BEFORE = re.compile(r"\bпозавчера\b", re.IGNORECASE)
_REL_LAST_FRIDAY = re.compile(
    r"(?:в\s+)?(?:прошлую|последнюю)\s+пятниц[уеё]",
    re.IGNORECASE,
)
_REL_FRIDAY = re.compile(r"\bв\s+пятниц[уеё]\b", re.IGNORECASE)
_DATE_DMY = re.compile(
    r"\b(\d{1,2})[.\s/](\d{1,2})(?:[.\s/](\d{2,4}))?\b"
)
_MONTH_NAMES = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "ма[йя]": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
}
_DATE_TEXT = re.compile(
    r"\b(\d{1,2})\s+(" + "|".join(_MONTH_NAMES) + r")[а-я]*(?:\s+(\d{4}))?\b",
    re.IGNORECASE,
)

from bot.texts.prompts.rag_search_planner import RAG_PLANNER_SYSTEM

_PLANNER_SYSTEM = RAG_PLANNER_SYSTEM


@dataclass
class RagRetrievalSettings:
    """Параметры глубины поиска (качество важнее скорости)."""

    planner_max_queries: int = 8
    top_k_per_query: int = 8
    max_chunks_merged: int = 24
    metadata_max_chunks: int = 24
    golden_top_k: int = 3
    golden_query_count: int = 4
    planner_max_tokens: int = 700
    history_tail_messages: int = 6


@dataclass
class RagSearchPlan:
    semantic_queries: List[str] = field(default_factory=list)
    target_dates_iso: List[str] = field(default_factory=list)
    prefer_content_types: List[str] = field(default_factory=list)
    source_substrings: List[str] = field(default_factory=list)
    telegram_message_id: Optional[int] = None
    telegram_chat_id: Optional[int] = None
    reason: str = ""


async def _log_rag_planner_usage(
    user_storage: "Database",
    *,
    user_id: int,
    model: str,
    usage: Any,
) -> None:
    request_id = str(uuid.uuid4())
    await user_storage.log_llm_completion_usage(
        user_id=user_id,
        provider="deepseek",
        model=model,
        usage=usage,
        request_kind="rag_search_planner",
        request_id=request_id,
    )
    pt, ct, tt, *_ = extract_token_counts_and_extras(usage)
    await user_storage.log_interaction(
        user_id=user_id,
        event_category="llm",
        event_type=f"deepseek_{model}_rag_search_planner",
        data={
            "provider": "deepseek",
            "request_id": request_id,
            "model": model,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": tt,
        },
    )


def _last_friday(on: date) -> date:
    d = on
    while d.weekday() != 4:
        d -= timedelta(days=1)
    if d == on:
        d -= timedelta(days=7)
    return d


def parse_dates_from_text(text: str, *, anchor: date) -> List[date]:
    found: List[date] = []
    t = text or ""

    if _REL_YESTERDAY.search(t):
        found.append(anchor - timedelta(days=1))
    if _REL_DAY_BEFORE.search(t):
        found.append(anchor - timedelta(days=2))
    if _REL_LAST_FRIDAY.search(t) or _REL_FRIDAY.search(t):
        found.append(_last_friday(anchor))

    for m in _DATE_DMY.finditer(t):
        day, month = int(m.group(1)), int(m.group(2))
        year_s = m.group(3)
        year = int(year_s) if year_s else anchor.year
        if year < 100:
            year += 2000
        try:
            found.append(date(year, month, day))
        except ValueError:
            pass

    for m in _DATE_TEXT.finditer(t):
        day = int(m.group(1))
        month_word = m.group(2).lower()
        month = 1
        for prefix, num in _MONTH_NAMES.items():
            if re.match(prefix, month_word, re.IGNORECASE):
                month = num
                break
        year_s = m.group(3)
        year = int(year_s) if year_s else anchor.year
        try:
            found.append(date(year, month, day))
        except ValueError:
            pass

    seen: set[date] = set()
    out: List[date] = []
    for d in found:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def parse_telegram_link(text: str) -> tuple[Optional[int], Optional[int]]:
    m = _TME_LINK_RE.search(text or "")
    if not m:
        return None, None
    chat = int(m.group(1))
    msg_id = int(m.group(2))
    if chat > 0:
        chat = int(f"-100{chat}")
    return chat, msg_id


def heuristic_plan(user_message: str, *, anchor: Optional[datetime] = None) -> RagSearchPlan:
    """Только даты и ссылки — без доменных слов (молитва и т.п.)."""
    dt = anchor or now_msk()
    anchor_date = dt.date() if hasattr(dt, "date") else dt
    dates = parse_dates_from_text(user_message, anchor=anchor_date)
    chat_id, msg_id = parse_telegram_link(user_message)

    queries = [user_message.strip()]
    for d in dates:
        queries.append(d.isoformat())

    source_subs = [d.isoformat() for d in dates]
    if msg_id:
        source_subs.append(str(msg_id))

    return RagSearchPlan(
        semantic_queries=_dedupe_str_list(queries),
        target_dates_iso=[d.isoformat() for d in dates],
        source_substrings=_dedupe_str_list(source_subs),
        telegram_message_id=msg_id,
        telegram_chat_id=chat_id,
        reason="heuristic",
    )


def _dedupe_str_list(items: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for x in items:
        s = (x or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _merge_plans(heuristic: RagSearchPlan, llm: RagSearchPlan, *, max_queries: int) -> RagSearchPlan:
    return RagSearchPlan(
        semantic_queries=_dedupe_str_list(
            llm.semantic_queries + heuristic.semantic_queries
        )[:max_queries],
        target_dates_iso=_dedupe_str_list(
            llm.target_dates_iso + heuristic.target_dates_iso
        )[:5],
        prefer_content_types=_dedupe_str_list(llm.prefer_content_types)[:3],
        source_substrings=_dedupe_str_list(
            llm.source_substrings + heuristic.source_substrings
        )[:5],
        telegram_message_id=heuristic.telegram_message_id or llm.telegram_message_id,
        telegram_chat_id=heuristic.telegram_chat_id or llm.telegram_chat_id,
        reason=llm.reason or heuristic.reason,
    )


async def plan_with_llm(
    client: "AsyncOpenAI",
    *,
    model: str,
    user_message: str,
    heuristic: RagSearchPlan,
    datetime_ctx: str,
    history_tail: Optional[str] = None,
    settings: Optional[RagRetrievalSettings] = None,
    user_id: Optional[int] = None,
    user_storage: Optional["Database"] = None,
) -> RagSearchPlan:
    settings = settings or RagRetrievalSettings()
    user_block = (
        f"{datetime_ctx}\n\n"
        f"Вопрос пользователя:\n{user_message}\n\n"
        f"Эвристики (подсказка, можно уточнить):\n"
        f"- target_dates_iso: {heuristic.target_dates_iso}\n"
        f"- source_substrings: {heuristic.source_substrings}\n"
        f"- telegram msg_id: {heuristic.telegram_message_id}\n"
        f"- черновые semantic_queries: {heuristic.semantic_queries[:4]}\n"
    )
    if history_tail:
        user_block += f"\nКонец истории диалога:\n{history_tail}\n"

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _PLANNER_SYSTEM},
                {"role": "user", "content": user_block},
            ],
            temperature=0.2,
            max_tokens=settings.planner_max_tokens,
        )
        if user_id is not None and user_storage is not None:
            await _log_rag_planner_usage(
                user_storage,
                user_id=user_id,
                model=model,
                usage=getattr(resp, "usage", None),
            )
        raw = (resp.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"\s*```\s*$", "", raw)
        data = json.loads(raw)
        llm_plan = RagSearchPlan(
            semantic_queries=_dedupe_str_list(
                [str(q) for q in (data.get("semantic_queries") or []) if q]
            ),
            target_dates_iso=_dedupe_str_list(
                [str(d) for d in (data.get("target_dates_iso") or []) if d]
            ),
            prefer_content_types=_dedupe_str_list(
                [str(p) for p in (data.get("prefer_content_types") or []) if p]
            ),
            source_substrings=_dedupe_str_list(
                [str(s) for s in (data.get("source_substrings") or []) if s]
            ),
            telegram_message_id=heuristic.telegram_message_id,
            telegram_chat_id=heuristic.telegram_chat_id,
            reason=str(data.get("reason") or "llm"),
        )
        return _merge_plans(heuristic, llm_plan, max_queries=settings.planner_max_queries)
    except Exception as e:
        logger.warning("RAG planner LLM failed, heuristic only: %s", e)
        return RagSearchPlan(
            semantic_queries=heuristic.semantic_queries[: settings.planner_max_queries],
            target_dates_iso=heuristic.target_dates_iso,
            prefer_content_types=heuristic.prefer_content_types,
            source_substrings=heuristic.source_substrings,
            telegram_message_id=heuristic.telegram_message_id,
            telegram_chat_id=heuristic.telegram_chat_id,
            reason="heuristic_fallback",
        )


def _chunk_key(block: str) -> str:
    return (block.split("\n", 1)[0] if block else "")[:220]


def _split_chunks(text: str) -> List[str]:
    return [c.strip() for c in (text or "").split("\n\n") if c.strip()]


def _merge_chunk_lists(chunk_lists: List[List[str]], *, max_chunks: int) -> str:
    seen: set[str] = set()
    parts: List[str] = []
    for chunks in chunk_lists:
        for c in chunks:
            key = _chunk_key(c)
            if key in seen:
                continue
            seen.add(key)
            parts.append(c)
            if len(parts) >= max_chunks:
                break
        if len(parts) >= max_chunks:
            break
    return "\n\n".join(parts)


def _chunk_matches_date(chunk: str, target_dates: List[str]) -> bool:
    if not target_dates or not chunk:
        return False
    blob = chunk[:600].lower()
    return any(d in blob or d[:7] in blob for d in target_dates)


def _chunk_matches_substring(chunk: str, substrings: List[str]) -> bool:
    if not substrings or not chunk:
        return False
    blob = chunk[:600].lower()
    return any(s.lower() in blob for s in substrings if s)


def _prioritize_chunks(
    merged: str,
    plan: RagSearchPlan,
    *,
    max_chunks: int,
) -> str:
    """Сначала чанки с нужной датой/ссылкой/подстрокой, затем остальные — без отбрасывания контекста."""
    chunks = _split_chunks(merged)
    if not chunks:
        return ""

    priority: List[str] = []
    rest: List[str] = []
    for c in chunks:
        if plan.telegram_message_id and f"/{plan.telegram_message_id}" in c:
            priority.append(c)
        elif plan.target_dates_iso and _chunk_matches_date(c, plan.target_dates_iso):
            priority.append(c)
        elif plan.source_substrings and _chunk_matches_substring(
            c, plan.source_substrings
        ):
            priority.append(c)
        else:
            rest.append(c)

    ordered = (priority + rest)[:max_chunks]
    return "\n\n".join(ordered)


def _retrieve_by_metadata_scan(
    rag_stack: "RagStack",
    *,
    source_substring: Optional[str] = None,
    message_link_suffix: Optional[str] = None,
    max_chunks: int = 24,
) -> List[str]:
    coll = rag_stack.vectors.expert_collection
    raw = coll.get(include=["documents", "metadatas"], limit=10_000)
    parts: List[str] = []
    for doc, meta in zip(raw.get("documents") or [], raw.get("metadatas") or []):
        m = meta or {}
        src = str(m.get("source") or "")
        links = list(metadata_link_fields_for_scan(m))
        link_blob = " ".join(links)
        if source_substring and source_substring not in src and source_substring not in str(
            m.get("date") or ""
        ):
            continue
        if message_link_suffix and message_link_suffix not in link_blob:
            continue
        text = (doc or "").strip()
        if not text:
            continue
        parts.append(format_retrieval_line(m, text))
        if len(parts) >= max_chunks:
            break
    return parts


async def _metadata_scan_async(rag_stack: "RagStack", **kwargs: Any) -> List[str]:
    return await asyncio.to_thread(_retrieve_by_metadata_scan, rag_stack, **kwargs)


async def execute_rag_search(
    rag_stack: "RagStack",
    plan: RagSearchPlan,
    *,
    fallback_query: str,
    settings: Optional[RagRetrievalSettings] = None,
) -> str:
    """Metadata + семантика + фильтры по content_type; всё сливается."""
    settings = settings or RagRetrievalSettings()
    chunk_lists: List[List[str]] = []

    if plan.telegram_message_id:
        chunk_lists.append(
            await _metadata_scan_async(
                rag_stack,
                message_link_suffix=f"/{plan.telegram_message_id}",
                max_chunks=settings.metadata_max_chunks,
            )
        )

    for iso in plan.target_dates_iso:
        chunk_lists.append(
            await _metadata_scan_async(
                rag_stack,
                source_substring=iso,
                max_chunks=settings.metadata_max_chunks,
            )
        )

    for sub in plan.source_substrings:
        if sub in plan.target_dates_iso:
            continue
        chunk_lists.append(
            await _metadata_scan_async(
                rag_stack,
                source_substring=sub,
                max_chunks=settings.metadata_max_chunks,
            )
        )

    all_queries = _dedupe_str_list(
        plan.semantic_queries + [fallback_query]
    )[: settings.planner_max_queries + 1]

    async def _sem(q: str, where: Optional[Dict[str, Any]] = None) -> List[str]:
        async with _RAG_QUERY_SEMAPHORE:
            part = await rag_stack.retriever.retrieve_context_async(
                q, top_k=settings.top_k_per_query, where=where
            )
        return _split_chunks(part)

    sem_tasks = [_sem(q) for q in all_queries]
    for ctype in plan.prefer_content_types:
        ctype_s = (ctype or "").strip()
        if not ctype_s:
            continue
        base_q = all_queries[0] if all_queries else fallback_query
        sem_tasks.append(_sem(base_q, where={"content_type": ctype_s}))

    sem_results = await asyncio.gather(*sem_tasks, return_exceptions=True)
    for i, res in enumerate(sem_results):
        if isinstance(res, Exception):
            logger.warning("RAG semantic sub-query %s failed: %s", i, res)
            sem_results[i] = []
    chunk_lists.extend(sem_results)

    merged = _merge_chunk_lists(chunk_lists, max_chunks=settings.max_chunks_merged * 2)
    return _prioritize_chunks(
        merged, plan, max_chunks=settings.max_chunks_merged
    )


async def _golden_merged(
    rag_stack: "RagStack",
    plan: RagSearchPlan,
    user_message: str,
    *,
    settings: RagRetrievalSettings,
) -> str:
    queries = _dedupe_str_list(
        plan.semantic_queries + [user_message]
    )[: settings.golden_query_count]
    blocks: List[str] = []
    for q in queries:
        block = await rag_stack.golden.format_few_shot_block_async(
            q, top_k=settings.golden_top_k
        )
        if block and block.strip():
            blocks.append(block.strip())
    return "\n\n---\n\n".join(blocks)


def build_history_tail(
    history: List[Dict[str, Any]], *, max_messages: int = 6, max_chars: int = 500
) -> str:
    lines: List[str] = []
    for msg in history[-max_messages:]:
        role = "Пользователь" if msg.get("role") == "user" else "Ассистент"
        lines.append(f"{role}: {(msg.get('content') or '')[:max_chars]}")
    return "\n".join(lines)


async def retrieve_for_user_message(
    rag_stack: "RagStack",
    user_message: str,
    *,
    llm_client: "AsyncOpenAI",
    llm_model: str = "deepseek-chat",
    history_tail: Optional[str] = None,
    settings: Optional[RagRetrievalSettings] = None,
    user_id: Optional[int] = None,
    user_storage: Optional["Database"] = None,
) -> tuple[str, str, str, RagSearchPlan]:
    """
    Полный цикл: datetime → эвристики → LLM-план → RAG → golden.

    Returns: (expert_context, golden_block, datetime_ctx, plan)
    """
    settings = settings or RagRetrievalSettings()
    datetime_ctx = format_datetime_context()
    heuristic = heuristic_plan(user_message)

    plan = await plan_with_llm(
        llm_client,
        model=llm_model,
        user_message=user_message,
        heuristic=heuristic,
        datetime_ctx=datetime_ctx,
        history_tail=history_tail,
        settings=settings,
        user_id=user_id,
        user_storage=user_storage,
    )

    expert, golden = await asyncio.gather(
        execute_rag_search(
            rag_stack,
            plan,
            fallback_query=user_message,
            settings=settings,
        ),
        _golden_merged(rag_stack, plan, user_message, settings=settings),
    )

    logger.info(
        "RAG plan reason=%s dates=%s queries=%s substrings=%s expert_len=%s golden_len=%s",
        plan.reason,
        plan.target_dates_iso,
        plan.semantic_queries,
        plan.source_substrings,
        len(expert),
        len(golden),
    )
    return expert, golden, datetime_ctx, plan
