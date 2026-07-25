"""Пайплайн: classify → RAG → ответ (с логированием токенов)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Optional

from bot.services.club_churn_report import load_aboutclub_text
from bot.services.llm_call_logger import logged_deepseek_chat
from bot.services.llm_request_kinds import (
    CLUB_TOPIC_ASSIST_ANSWER,
    CLUB_TOPIC_ASSIST_CLASSIFY,
)
from bot.texts.prompts.club_topic_assist import (
    TOPIC_ASSIST_ANSWER_SYSTEM,
    TOPIC_ASSIST_CLASSIFY_SYSTEM,
)
from config import config
from openai_client.rag_search_planner import (
    RagRetrievalSettings,
    retrieve_for_user_message,
)

if TYPE_CHECKING:
    from openai import AsyncOpenAI
    from rag.runtime import RagStack

logger = logging.getLogger(__name__)

Visibility = Literal["ephemeral", "public"]


@dataclass(frozen=True)
class ClassifyResult:
    intervene: bool
    visibility: Visibility
    reason: str


@dataclass(frozen=True)
class AssistResult:
    answer: str
    visibility: Visibility
    classify: ClassifyResult
    rag_used: bool


def _strip_json(raw: str) -> str:
    t = (raw or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def _parse_classify(raw: Optional[str]) -> ClassifyResult:
    if not raw:
        return ClassifyResult(False, "ephemeral", "empty_classify")
    try:
        data = json.loads(_strip_json(raw))
    except Exception:
        return ClassifyResult(False, "ephemeral", "bad_json")
    intervene = bool(data.get("intervene"))
    vis = str(data.get("visibility") or "ephemeral").strip().lower()
    if vis not in ("ephemeral", "public"):
        vis = "ephemeral"
    reason = str(data.get("reason") or "")[:300]
    return ClassifyResult(intervene, vis, reason)  # type: ignore[arg-type]


async def classify_topic_message(
    user_storage,
    *,
    user_id: int,
    question: str,
    context_tail: str,
    api_key: str,
) -> ClassifyResult:
    user_block = (
        f"Контекст последних сообщений этого пользователя:\n"
        f"{context_tail or '(нет)'}\n\n"
        f"Текущее сообщение:\n{question}"
    )
    raw, _ = await logged_deepseek_chat(
        user_storage,
        user_id=user_id,
        request_kind=CLUB_TOPIC_ASSIST_CLASSIFY,
        api_key=api_key,
        system=TOPIC_ASSIST_CLASSIFY_SYSTEM,
        user=user_block,
        temperature=0.1,
        max_tokens=180,
        timeout_sec=40.0,
        metadata={"feature": "club_topic_assist"},
    )
    result = _parse_classify(raw)
    if result.visibility == "public" and not config.CLUB_TOPIC_ASSIST_PUBLIC_ENABLED:
        return ClassifyResult(
            result.intervene, "ephemeral", f"{result.reason}|public_disabled"
        )
    return result


async def _rag_blocks(
    *,
    question: str,
    context_tail: str,
    user_id: int,
    user_storage,
    llm_client: Optional["AsyncOpenAI"],
    rag_stack: Optional["RagStack"],
) -> tuple[str, str, bool]:
    about = load_aboutclub_text()
    if not config.RAG_ENABLED or rag_stack is None or llm_client is None:
        return about, "", False
    try:
        settings = RagRetrievalSettings(
            planner_max_queries=4,
            top_k_per_query=6,
            max_chunks_merged=12,
            metadata_max_chunks=10,
            golden_top_k=2,
            golden_query_count=2,
            planner_max_tokens=600,
            history_tail_messages=6,
        )
        expert, golden, _, _plan = await retrieve_for_user_message(
            rag_stack,
            question,
            llm_client=llm_client,
            llm_model="deepseek-v4-flash",
            history_tail=context_tail or None,
            settings=settings,
            user_id=user_id,
            user_storage=user_storage,
        )
        return expert or about, golden or "", True
    except Exception as e:
        logger.warning("topic assist RAG failed: %s", e)
        return about, "", False


async def compose_topic_answer(
    user_storage,
    *,
    user_id: int,
    question: str,
    context_tail: str,
    api_key: str,
    llm_client: Optional["AsyncOpenAI"] = None,
    rag_stack: Optional["RagStack"] = None,
) -> AssistResult:
    classify = await classify_topic_message(
        user_storage,
        user_id=user_id,
        question=question,
        context_tail=context_tail,
        api_key=api_key,
    )
    if not classify.intervene:
        return AssistResult(
            answer="",
            visibility=classify.visibility,
            classify=classify,
            rag_used=False,
        )

    expert, golden, rag_used = await _rag_blocks(
        question=question,
        context_tail=context_tail,
        user_id=user_id,
        user_storage=user_storage,
        llm_client=llm_client,
        rag_stack=rag_stack,
    )
    user_block = (
        f"Контекст пользователя:\n{context_tail or '(нет)'}\n\n"
        f"Вопрос:\n{question}\n\n"
        f"CONTEXT (about / RAG):\n{(expert or '')[:6000]}\n\n"
        f"GOLDEN:\n{(golden or '')[:2000]}"
    )
    answer, _ = await logged_deepseek_chat(
        user_storage,
        user_id=user_id,
        request_kind=CLUB_TOPIC_ASSIST_ANSWER,
        api_key=api_key,
        system=TOPIC_ASSIST_ANSWER_SYSTEM,
        user=user_block,
        temperature=0.35,
        max_tokens=500,
        timeout_sec=60.0,
        metadata={
            "feature": "club_topic_assist",
            "visibility": classify.visibility,
            "rag_used": rag_used,
        },
    )
    text = (answer or "").strip()
    if not text:
        text = (
            "Не смог уверенно ответить по материалам клуба. "
            "Напишите, пожалуйста, в поддержку — там помогут точнее."
        )
    return AssistResult(
        answer=text,
        visibility=classify.visibility,
        classify=classify,
        rag_used=rag_used,
    )
