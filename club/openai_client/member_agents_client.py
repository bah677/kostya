"""
Клиент member-агента для участников с активной лицензией.

Sales-агент (AgentsClient) не меняется.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import TYPE_CHECKING, Optional

from openai import AsyncOpenAI

from bot.followup_segments import sensitive_context_system_addon
from bot.services.club_schedule_service import (
    fetch_schedule_allowed_links,
    fetch_schedule_for_prompt,
    schedule_admin_dm_addon,
    try_apply_schedule_from_admin_dm,
)
from bot.services.member_license_context import (
    access_reply_is_too_vague,
    build_access_status_reply_html,
    build_member_license_facts_addon,
    build_natural_language_turn_addon,
    looks_like_access_question,
)
from bot.services.member_profile_service import (
    after_member_agent_reply,
    prepare_member_dm_turn,
)
from bot.services.bot_capabilities_knowledge import load_member_bot_capabilities
from bot.texts.prompts.agents_club_member import build_club_member_system_prompt
from bot.texts.prompts.followup_segments import SENSITIVE_AGENT_ADDON
from bot.texts.prompts.member_rag_augmentation import augment_member_system_prompt_with_rag
from bot.texts.ru_member_agent import MEMBER_AGENT_FALLBACK_HTML
from config import config
from openai_client.agent_verifier_loop import run_with_verifier_retries
from openai_client.agents_client import AgentsClient, DeepSeekTimeoutError
from openai_client.member_agent_verifier import extract_allowed_links_from_context
from openai_client.rag_search_planner import (
    RagRetrievalSettings,
    build_history_tail,
    retrieve_for_user_message,
)
from bot.services.agent_datetime_context import prepend_datetime_context
from storage.db.llm_token_normalize import extract_token_counts_and_extras

if TYPE_CHECKING:
    from rag.runtime import RagStack

logger = logging.getLogger(__name__)

MEMBER_RAG_SETTINGS = RagRetrievalSettings(
    planner_max_queries=10,
    top_k_per_query=10,
    max_chunks_merged=32,
    metadata_max_chunks=28,
    golden_top_k=4,
    golden_query_count=5,
)


class MemberAgentsClient(AgentsClient):
    """Агент для участников клуба (после оплаты)."""

    def __init__(
        self,
        user_storage,
        *,
        rag_stack: Optional["RagStack"] = None,
    ):
        self.user_storage = user_storage
        self.rag_stack = rag_stack
        self._use_static_system_prompt = False

        self.client = AsyncOpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com/v1",
            timeout=30.0,
            max_retries=2,
        )

        self.club_knowledge = self._load_club_knowledge()
        self.bot_capabilities = load_member_bot_capabilities()
        self.system_prompt = build_club_member_system_prompt(
            self.club_knowledge,
            bot_capabilities=self.bot_capabilities,
        )
        if rag_stack is not None:
            ne, ng = rag_stack.expert_count_golden_count()
            logger.info(
                "✅ MemberAgentsClient: RAG (expert≈%s, golden≈%s)",
                ne,
                ng,
            )
        logger.info(
            "✅ MemberAgentsClient: member prompt (%s символов)",
            len(self.system_prompt),
        )

    async def run(self, user_message: str, user_id: int) -> Optional[str]:
        try:
            history = await self.user_storage.get_private_chat_history(
                user_id, limit=self.HISTORY_LIMIT
            )

            sensitive = sensitive_context_system_addon(user_message, history)
            retrieved, golden_block = "", ""
            rag_plan = None

            if sensitive:
                system_content = self.system_prompt + SENSITIVE_AGENT_ADDON
                logger.info(
                    "🔴 Member sensitive context user %s — no RAG",
                    user_id,
                )
            else:
                if self.rag_stack is not None:
                    history_tail = (
                        build_history_tail(history, max_messages=6)
                        if history
                        else None
                    )
                    rag_settings = MEMBER_RAG_SETTINGS
                    retrieved, golden_block, _, rag_plan = await retrieve_for_user_message(
                        self.rag_stack,
                        user_message,
                        llm_client=self.client,
                        llm_model=self.CHAT_MODEL,
                        history_tail=history_tail,
                        settings=rag_settings,
                        user_id=user_id,
                        user_storage=self.user_storage,
                    )
                    if retrieved or golden_block:
                        logger.info(
                            "📚 Member RAG user %s expert=%s golden=%s",
                            user_id,
                            len(retrieved or ""),
                            len(golden_block or ""),
                        )
                base = self.system_prompt
                system_content = augment_member_system_prompt_with_rag(
                    base,
                    retrieved_context=retrieved or "",
                    golden_block=golden_block or "",
                )

            system_content = self._attach_datetime_context(system_content)
            system_content += self._no_greeting_context_addon(history)

            admin_schedule_note = ""
            if await self.user_storage.is_telegram_admin_id(user_id):
                dm_sched = await try_apply_schedule_from_admin_dm(
                    self.user_storage,
                    self.client,
                    user_id,
                    user_message,
                )
                if dm_sched and dm_sched.applied:
                    admin_schedule_note = schedule_admin_dm_addon(dm_sched)

            schedule_addon = await fetch_schedule_for_prompt(self.user_storage)
            system_content += "\n\n" + schedule_addon
            if admin_schedule_note:
                system_content += "\n\n" + admin_schedule_note

            profile_addon = await prepare_member_dm_turn(
                self.user_storage, user_id, user_message
            )
            license_row = await self.user_storage.get_user_active_license(user_id)
            license_addon = build_member_license_facts_addon(license_row)
            turn_addon = build_natural_language_turn_addon(user_message)
            for block in (profile_addon, license_addon, turn_addon):
                if block:
                    system_content += "\n\n" + block

            if config.MEMBER_GOALS_EXTRACT_ENABLED:
                from bot.services.member_goals_extractor import (
                    extract_and_append_member_goals_safe,
                )

                asyncio.create_task(
                    extract_and_append_member_goals_safe(
                        user_storage=self.user_storage,
                        llm_client=self.client,
                        user_id=user_id,
                        user_message=user_message,
                        history=history,
                    ),
                    name=f"member_goals_{user_id}",
                )

            messages = [{"role": "system", "content": system_content}]
            for msg in history:
                messages.append({"role": msg["role"], "content": msg["content"]})

            history_user_message_appended = False
            if (
                not history
                or history[-1]["role"] != "user"
                or history[-1]["content"] != user_message
            ):
                messages.append({"role": "user", "content": user_message})
                history_user_message_appended = True

            self._log_agent_request_dump(
                user_id=user_id,
                user_message=user_message,
                history=history,
                messages=messages,
                sensitive=bool(sensitive),
                retrieved=retrieved,
                golden_block=golden_block,
                rag_plan=rag_plan,
                history_user_message_appended=history_user_message_appended,
            )

            verification_context = "\n\n".join(
                p
                for p in (
                    profile_addon,
                    license_addon,
                    schedule_addon,
                    admin_schedule_note,
                    retrieved,
                    golden_block,
                    self.club_knowledge[:8000],
                )
                if p
            )
            allowed_links = list(
                dict.fromkeys(
                    extract_allowed_links_from_context(retrieved, golden_block)
                    + await fetch_schedule_allowed_links(self.user_storage)
                )
            )

            async def _generate(extra_system: str = "") -> Optional[str]:
                gen_messages = list(messages)
                if extra_system:
                    gen_messages[0] = {
                        "role": "system",
                        "content": system_content + extra_system,
                    }
                response = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model=self.CHAT_MODEL,
                        messages=gen_messages,
                        temperature=0.65,
                        max_tokens=2048,
                    ),
                    timeout=25.0,
                )
                usage = getattr(response, "usage", None)
                request_id = str(uuid.uuid4())
                await self.user_storage.log_llm_completion_usage(
                    user_id=user_id,
                    provider="deepseek",
                    model=self.CHAT_MODEL,
                    usage=usage,
                    request_kind="member_chat_completion",
                    request_id=request_id,
                )
                pt, ct, tt, *_ = extract_token_counts_and_extras(usage)
                await self.user_storage.log_interaction(
                    user_id=user_id,
                    event_category="llm",
                    event_type=f"deepseek_{self.CHAT_MODEL}_member_chat_completion",
                    data={
                        "provider": "deepseek",
                        "request_id": request_id,
                        "model": self.CHAT_MODEL,
                        "prompt_tokens": pt,
                        "completion_tokens": ct,
                        "total_tokens": tt,
                    },
                    source="deepseek",
                    outcome="success",
                )
                return response.choices[0].message.content

            if sensitive or not config.MEMBER_AGENT_VERIFIER_ENABLED:
                draft = await _generate()
            else:
                draft = await run_with_verifier_retries(
                    generate=_generate,
                    verify_kwargs={
                        "client": self.client,
                        "user_message": user_message,
                        "verification_context": verification_context,
                        "allowed_links": allowed_links,
                        "user_id": user_id,
                        "user_storage": self.user_storage,
                    },
                    max_retries=config.MEMBER_AGENT_VERIFIER_MAX_RETRIES,
                    fallback=MEMBER_AGENT_FALLBACK_HTML,
                    user_id=user_id,
                )

            if draft:
                if looks_like_access_question(user_message) and access_reply_is_too_vague(
                    draft,
                    has_active_license=bool(license_row),
                ):
                    factual = build_access_status_reply_html(license_row)
                    logger.info(
                        "Member access reply replaced vague draft user=%s",
                        user_id,
                    )
                    draft = factual
                await after_member_agent_reply(self.user_storage, user_id, draft)
            return draft

        except asyncio.TimeoutError as e:
            logger.error("❌ Member DeepSeek timeout user %s", user_id)
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type="deepseek_timeout",
                data={"model": self.CHAT_MODEL, "agent": "member"},
                source="deepseek",
                outcome="error",
            )
            raise DeepSeekTimeoutError(
                f"DeepSeek timeout for user {user_id}"
            ) from e
        except DeepSeekTimeoutError:
            raise
        except Exception as e:
            logger.error("❌ Member agent error user %s: %s", user_id, e)
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type="member_agent_error",
                data={"error": str(e)},
                source="deepseek",
                outcome="error",
            )
            return None
