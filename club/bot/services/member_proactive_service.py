"""Проактивные сообщения member-агента: planner + compose + отправка."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from zoneinfo import ZoneInfo

from aiogram.enums import ParseMode
from openai import AsyncOpenAI

from bot.services.club_schedule_service import (
    fetch_schedule_allowed_links,
    fetch_schedule_for_prompt,
)
from bot.services.member_profile_service import (
    after_member_agent_reply,
    build_member_profile_prompt_addon,
)
from bot.services.agent_datetime_context import prepend_datetime_context
from bot.texts.prompts.member_proactive import (
    PROACTIVE_COMPOSE_SYSTEM,
    PROACTIVE_PLANNER_SYSTEM,
)
from config import config
from openai_client.agent_verifier_loop import run_with_verifier_retries
from openai_client.member_agent_verifier import extract_allowed_links_from_context
from openai_client.rag_search_planner import retrieve_for_user_message
from openai_client.member_agents_client import MEMBER_RAG_SETTINGS
from storage.db.llm_token_normalize import extract_token_counts_and_extras

if TYPE_CHECKING:
    from rag.runtime import RagStack

logger = logging.getLogger(__name__)

CHAT_MODEL = "deepseek-chat"
MSK = ZoneInfo("Europe/Moscow")


def _strip_json_fence(raw: str) -> str:
    t = (raw or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def proactive_slug_for_today(today_msk: datetime) -> str:
    return f"proactive_{today_msk.strftime('%Y-%m-%d')}"


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


async def plan_proactive_for_user(
    llm_client: AsyncOpenAI,
    user_storage,
    user_id: int,
    *,
    profile: Optional[Dict[str, Any]],
    schedule_addon: str,
    days_to_expiry: Optional[int],
) -> Dict[str, Any]:
    profile_addon = build_member_profile_prompt_addon(profile)
    user_block = (
        f"user_id: {user_id}\n"
        f"Дней до конца лицензии: {days_to_expiry if days_to_expiry is not None else '—'}\n\n"
        f"{profile_addon}\n\n{schedule_addon}"
    )
    try:
        resp = await llm_client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": prepend_datetime_context(PROACTIVE_PLANNER_SYSTEM)},
                {"role": "user", "content": user_block},
            ],
            temperature=0.25,
            max_tokens=600,
        )
        await _log_llm(user_storage, user_id, "proactive_planner", resp.usage)
        data = json.loads(_strip_json_fence(resp.choices[0].message.content or ""))
        return {
            "should_send": bool(data.get("should_send")),
            "goal": str(data.get("goal") or "none"),
            "reason": str(data.get("reason") or ""),
            "composer_hint": str(data.get("composer_hint") or ""),
        }
    except Exception as e:
        logger.warning("proactive planner uid=%s: %s", user_id, e)
        return {"should_send": False, "goal": "none", "reason": "", "composer_hint": ""}


async def compose_proactive_message(
    *,
    user_storage,
    llm_client: AsyncOpenAI,
    rag_stack: Optional["RagStack"],
    user_id: int,
    plan: Dict[str, Any],
    schedule_addon: str,
    profile: Optional[Dict[str, Any]],
) -> Optional[str]:
    profile_addon = build_member_profile_prompt_addon(profile)
    retrieved = ""
    if rag_stack is not None and plan.get("composer_hint"):
        try:
            retrieved, _, _, _ = await retrieve_for_user_message(
                rag_stack,
                plan["composer_hint"],
                llm_client=llm_client,
                llm_model=CHAT_MODEL,
                history_tail=None,
                settings=MEMBER_RAG_SETTINGS,
                user_id=user_id,
                user_storage=user_storage,
            )
        except Exception as e:
            logger.warning("proactive RAG uid=%s: %s", user_id, e)

    user_block = (
        f"Цель проактива: {plan.get('goal')}\n"
        f"Подсказка: {plan.get('composer_hint')}\n\n"
        f"{profile_addon}\n\n{schedule_addon}\n"
    )
    if retrieved:
        user_block += f"\nМатериалы:\n{retrieved[:6000]}"

    verification_context = "\n\n".join(
        p for p in (profile_addon, schedule_addon, retrieved) if p
    )
    allowed_links = list(
        dict.fromkeys(
            extract_allowed_links_from_context(retrieved)
            + await fetch_schedule_allowed_links(user_storage)
        )
    )

    async def _generate(extra: str = "") -> Optional[str]:
        resp = await llm_client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": prepend_datetime_context(PROACTIVE_COMPOSE_SYSTEM + (extra or ""))},
                {"role": "user", "content": user_block},
            ],
            temperature=0.65,
            max_tokens=1000,
        )
        await _log_llm(user_storage, user_id, "proactive_compose", resp.usage)
        return resp.choices[0].message.content

    if config.MEMBER_AGENT_VERIFIER_ENABLED:
        return await run_with_verifier_retries(
            generate=_generate,
            verify_kwargs={
                "client": llm_client,
                "user_message": plan.get("composer_hint") or "проактив",
                "verification_context": verification_context,
                "allowed_links": allowed_links,
                "user_id": user_id,
                "user_storage": user_storage,
            },
            max_retries=config.MEMBER_AGENT_VERIFIER_MAX_RETRIES,
            fallback="",
            user_id=user_id,
        )
    draft = await _generate()
    return (draft or "").strip() or None


def _wrote_today_msk(last_dm_at: Any, today_msk_date) -> bool:
    if last_dm_at is None:
        return False
    if isinstance(last_dm_at, datetime):
        dm = last_dm_at
        if dm.tzinfo is None:
            dm = dm.replace(tzinfo=MSK)
        return dm.astimezone(MSK).date() == today_msk_date
    return False


async def run_proactive_batch(
    *,
    user_storage,
    bot,
    llm_client: AsyncOpenAI,
    rag_stack: Optional["RagStack"],
    today_msk_date,
    max_users: int,
) -> int:
    """Обрабатывает до max_users кандидатов. Возвращает число отправленных."""
    candidates = await user_storage.list_proactive_candidates(
        limit=max_users * 3,
    )
    sent = 0
    slug = proactive_slug_for_today(datetime.now(MSK))

    for row in candidates:
        if sent >= max_users:
            break
        uid = int(row["user_id"])
        if (
            await user_storage.get_proactive_sent_count_today(uid, today=today_msk_date)
            >= config.CLUB_OUTREACH_DAILY_LIMIT
        ):
            continue
        if not await user_storage.try_claim_subscription_outreach(
            uid, slug, today_msk_date
        ):
            continue

        if _wrote_today_msk(row.get("last_dm_at"), today_msk_date):
            continue

        profile = await user_storage.get_member_profile(uid)
        if not await user_storage.proactive_slot_available(uid, profile=profile):
            continue

        schedule_addon = await fetch_schedule_for_prompt(user_storage)
        days_left = row.get("days_to_expiry")
        if days_left is not None:
            try:
                days_left = int(days_left)
            except (TypeError, ValueError):
                days_left = None

        plan = await plan_proactive_for_user(
            llm_client,
            user_storage,
            uid,
            profile=profile,
            schedule_addon=schedule_addon,
            days_to_expiry=days_left,
        )
        if not plan.get("should_send"):
            logger.info(
                "proactive skip uid=%s reason=%s",
                uid,
                plan.get("reason"),
            )
            continue

        body = await compose_proactive_message(
            user_storage=user_storage,
            llm_client=llm_client,
            rag_stack=rag_stack,
            user_id=uid,
            plan=plan,
            schedule_addon=schedule_addon,
            profile=profile,
        )
        if not body:
            continue

        try:
            from bot.utils.user_ui import with_main_menu

            await bot.send_message(
                uid,
                body,
                parse_mode=ParseMode.HTML,
                reply_markup=with_main_menu([]),
            )
            await after_member_agent_reply(user_storage, uid, body)
            await user_storage.record_proactive_sent(
                uid,
                goal=str(plan.get("goal") or ""),
                reason=str(plan.get("reason") or ""),
            )
            await user_storage.increment_proactive_sent_today(uid)
            sent += 1
            logger.info(
                "proactive sent uid=%s goal=%s",
                uid,
                plan.get("goal"),
            )
        except Exception as e:
            logger.error("proactive send uid=%s: %s", uid, e)

    return sent
