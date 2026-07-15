"""LLM + RAG пайплайн для сегмента «застряли в диалоге»."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from bot.services.agent_datetime_context import prepend_datetime_context
from openai_client.rag_search_planner import (
    RagRetrievalSettings,
    RagSearchPlan,
    build_history_tail,
    execute_rag_search,
    heuristic_plan,
    _golden_merged,
    _dedupe_str_list,
    _merge_plans,
)
from bot.texts.prompts.stuck_dialog import (
    STUCK_ANALYZER_SYSTEM,
    STUCK_COMPOSE_SYSTEM,
    STUCK_RAG_PLANNER_SYSTEM,
)
from storage.db.llm_token_normalize import extract_token_counts_and_extras

if TYPE_CHECKING:
    from openai import AsyncOpenAI
    from rag.runtime import RagStack
    from storage.database import Database

logger = logging.getLogger(__name__)

CHAT_MODEL = "deepseek-chat"


def stuck_rag_settings() -> RagRetrievalSettings:
    """Глубокий RAG для дожима (качество важнее скорости)."""
    return RagRetrievalSettings(
        planner_max_queries=12,
        top_k_per_query=12,
        max_chunks_merged=40,
        metadata_max_chunks=32,
        golden_top_k=5,
        golden_query_count=5,
        planner_max_tokens=1200,
        history_tail_messages=20,
    )


def _strip_json_fence(raw: str) -> str:
    t = (raw or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


async def _log_llm(
    user_storage: Optional["Database"],
    *,
    user_id: int,
    kind: str,
    model: str,
    usage: Any,
) -> None:
    if user_storage is None:
        return
    request_id = str(uuid.uuid4())
    await user_storage.log_llm_completion_usage(
        user_id=user_id,
        provider="deepseek",
        model=model,
        usage=usage,
        request_kind=kind,
        request_id=request_id,
    )
    pt, ct, tt, *_ = extract_token_counts_and_extras(usage)
    await user_storage.log_interaction(
        user_id=user_id,
        event_category="llm",
        event_type=f"deepseek_{model}_{kind}",
        data={
            "provider": "deepseek",
            "request_id": request_id,
            "model": model,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": tt,
        },
    )


async def analyze_dialog(
    client: "AsyncOpenAI",
    *,
    history: List[Dict[str, str]],
    user_id: int,
    user_storage: Optional["Database"] = None,
) -> Dict[str, Any]:
    tail = build_history_tail(history, max_messages=30, max_chars=800)
    resp = await client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": prepend_datetime_context(STUCK_ANALYZER_SYSTEM)},
            {
                "role": "user",
                "content": f"Переписка:\n{tail}",
            },
        ],
        temperature=0.25,
        max_tokens=2000,
    )
    await _log_llm(
        user_storage, user_id=user_id, kind="stuck_dialog_analyzer", model=CHAT_MODEL,
        usage=getattr(resp, "usage", None),
    )
    raw = _strip_json_fence(resp.choices[0].message.content or "")
    data = json.loads(raw)
    return {
        "topic_label": str(data.get("topic_label") or "").strip(),
        "stuck_point": str(data.get("stuck_point") or "").strip(),
        "user_need": str(data.get("user_need") or "").strip(),
        "rag_focus": str(data.get("rag_focus") or "").strip(),
        "tone_notes": str(data.get("tone_notes") or "").strip(),
        "ping_line": str(data.get("ping_line") or data.get("topic_label") or "").strip(),
        "sensitive": bool(data.get("sensitive")),
    }


async def plan_stuck_rag(
    client: "AsyncOpenAI",
    *,
    analysis: Dict[str, Any],
    history: List[Dict[str, str]],
    user_id: int,
    user_storage: Optional["Database"] = None,
    settings: Optional[RagRetrievalSettings] = None,
) -> RagSearchPlan:
    settings = settings or stuck_rag_settings()
    rag_query = (
        f"{analysis.get('rag_focus', '')}\n"
        f"Тема: {analysis.get('topic_label', '')}\n"
        f"Запрос пользователя: {analysis.get('user_need', '')}\n"
        f"Застряли: {analysis.get('stuck_point', '')}"
    ).strip()
    heuristic = heuristic_plan(rag_query)
    tail = build_history_tail(history, max_messages=settings.history_tail_messages)

    user_block = (
        f"Анализ диалога:\n{json.dumps(analysis, ensure_ascii=False, indent=2)}\n\n"
        f"Эвристики:\n"
        f"- target_dates_iso: {heuristic.target_dates_iso}\n"
        f"- semantic_queries (черновик): {heuristic.semantic_queries[:6]}\n\n"
        f"Хвост переписки:\n{tail}\n"
    )

    resp = await client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": prepend_datetime_context(STUCK_RAG_PLANNER_SYSTEM)},
            {"role": "user", "content": user_block},
        ],
        temperature=0.2,
        max_tokens=settings.planner_max_tokens,
    )
    await _log_llm(
        user_storage,
        user_id=user_id,
        kind="stuck_dialog_rag_planner",
        model=CHAT_MODEL,
        usage=getattr(resp, "usage", None),
    )
    raw = _strip_json_fence(resp.choices[0].message.content or "")
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
        reason=str(data.get("reason") or "stuck_llm"),
    )
    return _merge_plans(heuristic, llm_plan, max_queries=settings.planner_max_queries)


async def retrieve_stuck_materials(
    rag_stack: "RagStack",
    client: "AsyncOpenAI",
    *,
    analysis: Dict[str, Any],
    history: List[Dict[str, str]],
    user_id: int,
    user_storage: Optional["Database"] = None,
) -> tuple[str, str, RagSearchPlan]:
    settings = stuck_rag_settings()
    rag_query = (
        f"{analysis.get('rag_focus', '')} {analysis.get('topic_label', '')} "
        f"{analysis.get('user_need', '')}"
    ).strip()
    plan = await plan_stuck_rag(
        client,
        analysis=analysis,
        history=history,
        user_id=user_id,
        user_storage=user_storage,
        settings=settings,
    )
    expert = await execute_rag_search(
        rag_stack,
        plan,
        fallback_query=rag_query,
        settings=settings,
    )
    golden = await _golden_merged(
        rag_stack, plan, rag_query, settings=settings
    )
    return expert, golden, plan


async def compose_stuck_answer(
    client: "AsyncOpenAI",
    *,
    analysis: Dict[str, Any],
    expert_context: str,
    golden_block: str,
    user_id: int,
    user_storage: Optional["Database"] = None,
) -> str:
    rag_part = (expert_context or "").strip()
    if golden_block and golden_block.strip():
        rag_part = f"{rag_part}\n\n---\n\nПримеры тона ответов:\n{golden_block.strip()}"

    user_content = (
        f"Анализ:\n{json.dumps(analysis, ensure_ascii=False, indent=2)}\n\n"
        f"Фрагменты клуба (RAG):\n{rag_part or '(мало материалов в архиве)'}"
    )
    resp = await client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": prepend_datetime_context(STUCK_COMPOSE_SYSTEM)},
            {"role": "user", "content": user_content},
        ],
        temperature=0.35,
        max_tokens=4000,
    )
    await _log_llm(
        user_storage,
        user_id=user_id,
        kind="stuck_dialog_compose",
        model=CHAT_MODEL,
        usage=getattr(resp, "usage", None),
    )
    return (resp.choices[0].message.content or "").strip()


async def run_full_stuck_pipeline(
    *,
    user_id: int,
    user_storage: "Database",
    llm_client: "AsyncOpenAI",
    rag_stack: Optional["RagStack"],
) -> Dict[str, Any]:
    """
    Полный цикл подготовки контекста для stuck_dialog.
    Возвращает dict для сохранения в followup_states.stuck_context.
    """
    history = await user_storage.get_private_chat_history(user_id, limit=30)
    if not history:
        return {"error": "no_history", "sensitive": False}

    analysis = await analyze_dialog(
        llm_client, history=history, user_id=user_id, user_storage=user_storage
    )
    if analysis.get("sensitive"):
        return {"sensitive": True, "analysis": analysis}

    expert, golden, plan = "", "", None
    chunk_count = 0
    if rag_stack is not None:
        expert, golden, plan = await retrieve_stuck_materials(
            rag_stack,
            llm_client,
            analysis=analysis,
            history=history,
            user_id=user_id,
            user_storage=user_storage,
        )
        chunk_count = len([c for c in (expert or "").split("\n\n") if c.strip()])
    else:
        logger.warning("stuck_dialog: RAG stack missing for user %s", user_id)

    composed = await compose_stuck_answer(
        llm_client,
        analysis=analysis,
        expert_context=expert,
        golden_block=golden,
        user_id=user_id,
        user_storage=user_storage,
    )

    return {
        "sensitive": False,
        "analysis": analysis,
        "rag_plan": {
            "semantic_queries": plan.semantic_queries if plan else [],
            "reason": plan.reason if plan else "",
        },
        "chunk_count": chunk_count,
        "composed_answer": composed,
        "ping_line": analysis.get("ping_line") or analysis.get("topic_label") or "",
    }


async def compose_answer_from_cached_context(
    *,
    user_id: int,
    user_storage: "Database",
    llm_client: "AsyncOpenAI",
    stuck_context: Dict[str, Any],
    rag_stack: Optional["RagStack"],
) -> str:
    """Пересобрать ответ, если в кэше ещё нет composed_answer."""
    if (stuck_context.get("composed_answer") or "").strip():
        return stuck_context["composed_answer"].strip()

    analysis = stuck_context.get("analysis") or {}
    history = await user_storage.get_private_chat_history(user_id, limit=30)
    expert, golden = "", ""
    if rag_stack is not None:
        expert, golden, _ = await retrieve_stuck_materials(
            rag_stack,
            llm_client,
            analysis=analysis,
            history=history,
            user_id=user_id,
            user_storage=user_storage,
        )
    return await compose_stuck_answer(
        llm_client,
        analysis=analysis,
        expert_context=expert,
        golden_block=golden,
        user_id=user_id,
        user_storage=user_storage,
    )
