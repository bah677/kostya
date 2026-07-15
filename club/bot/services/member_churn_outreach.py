"""Персонализированные churn-сообщения после выхода (AI + fallback на шаблон)."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional, TYPE_CHECKING

from openai import AsyncOpenAI

from bot.services.club_schedule_service import fetch_schedule_for_prompt
from bot.services.member_profile_service import build_member_profile_prompt_addon
from bot.texts import ru_subscription_reminder as sub_txt
from bot.texts.prompts.member_churn_compose import CHURN_COMPOSE_SYSTEM
from config import config
from openai_client.agent_verifier_loop import run_with_verifier_retries
from openai_client.member_agent_verifier import extract_allowed_links_from_context
from bot.services.agent_datetime_context import prepend_datetime_context
from openai_client.rag_search_planner import retrieve_for_user_message
from openai_client.member_agents_client import MEMBER_RAG_SETTINGS
from storage.db.llm_token_normalize import extract_token_counts_and_extras

if TYPE_CHECKING:
    from rag.runtime import RagStack

logger = logging.getLogger(__name__)

CHAT_MODEL = "deepseek-chat"

# Опрос с фиксированными вариантами — только шаблон, иначе ломаются кнопки.
_CHURN_TEMPLATE_ONLY_SLUGS = frozenset({"churn_plus_18d"})


async def _log_llm(user_storage, user_id: int, kind: str, usage) -> None:
    request_id = str(uuid.uuid4())
    await user_storage.log_llm_completion_usage(
        user_id=user_id,
        provider="deepseek",
        model=CHAT_MODEL,
        usage=usage,
        request_kind=kind,
        request_id=request_id,
    )
    pt, ct, tt, *_ = extract_token_counts_and_extras(usage)
    await user_storage.log_interaction(
        user_id=user_id,
        event_category="llm",
        event_type=f"deepseek_{CHAT_MODEL}_{kind}",
        data={
            "request_id": request_id,
            "model": CHAT_MODEL,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": tt,
        },
        source="deepseek",
        outcome="success",
    )


async def generate_churn_outreach_html(
    *,
    user_storage,
    llm_client: AsyncOpenAI,
    rag_stack: Optional["RagStack"],
    user_id: int,
    first_name: Optional[str],
    churn_block: Dict[str, Any],
) -> str:
    """AI-сообщение churn; при ошибке или опросе +18 — шаблон CHURN_MESSAGES."""
    fallback = sub_txt.personalize_html(churn_block["text"], first_name)
    slug = str(churn_block.get("slug") or "")
    days_after = int(churn_block.get("days_after_exit") or 0)

    if slug in _CHURN_TEMPLATE_ONLY_SLUGS or not config.MEMBER_CHURN_AI_ENABLED:
        return fallback

    profile = await user_storage.get_member_profile(user_id)
    profile_addon = build_member_profile_prompt_addon(profile)
    schedule_addon = await fetch_schedule_for_prompt(user_storage)

    retrieved, golden_block = "", ""
    rag_query = (
        f"клуб участник возврат ценность эфиры молитва "
        f"после выхода {days_after} дней"
    )
    if rag_stack is not None:
        try:
            retrieved, golden_block, _, _ = await retrieve_for_user_message(
                rag_stack,
                rag_query,
                llm_client=llm_client,
                llm_model=CHAT_MODEL,
                history_tail=None,
                settings=MEMBER_RAG_SETTINGS,
                user_id=user_id,
                user_storage=user_storage,
            )
        except Exception as e:
            logger.warning("churn RAG uid=%s: %s", user_id, e)

    user_block = (
        f"Дней после выхода из клуба: {days_after}\n"
        f"Имя: {first_name or 'участник'}\n\n"
        f"{profile_addon}\n\n{schedule_addon}\n\n"
        f"ЭТАЛОН (тон и структура, не копировать дословно):\n"
        f"{fallback}\n\n"
    )
    if retrieved:
        user_block += f"Фрагменты материалов клуба:\n{retrieved[:8000]}\n"

    verification_context = "\n\n".join(
        p for p in (profile_addon, schedule_addon, retrieved, fallback) if p
    )
    allowed_links = extract_allowed_links_from_context(retrieved, golden_block)

    async def _generate(extra: str = "") -> Optional[str]:
        sys = prepend_datetime_context(CHURN_COMPOSE_SYSTEM + (extra or ""))
        resp = await llm_client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": user_block},
            ],
            temperature=0.65,
            max_tokens=1200,
        )
        await _log_llm(user_storage, user_id, "churn_compose", resp.usage)
        return resp.choices[0].message.content

    if not config.MEMBER_AGENT_VERIFIER_ENABLED:
        draft = await _generate()
        return (draft or "").strip() or fallback

    draft = await run_with_verifier_retries(
        generate=_generate,
        verify_kwargs={
            "client": llm_client,
            "user_message": f"churn сообщение +{days_after} дней после выхода",
            "verification_context": verification_context,
            "allowed_links": allowed_links,
            "user_id": user_id,
            "user_storage": user_storage,
        },
        max_retries=config.MEMBER_AGENT_VERIFIER_MAX_RETRIES,
        fallback=fallback,
        user_id=user_id,
    )
    return (draft or "").strip() or fallback
