"""Оркестрация LLM-агентов челленджа чтения Писания."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from openai_client.agents_client import AgentsClient
from openai_client.scripture_challenge_prompts import (
    CHALLENGE_CHAT_SYSTEM_PROMPT,
    DAILY_COMMENT_SYSTEM_PROMPT,
    INTAKE_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    REVIEWER_SYSTEM_PROMPT,
    WEEKLY_REVIEW_CONTROLLER_PROMPT,
    WEEKLY_REVIEW_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

MAX_PLAN_REVISION_ROUNDS = 3
MAX_WEEKLY_REVISION_ROUNDS = 2
INTAKE_READY_MARKER = "INTAKE_READY"
INTAKE_CANCEL_MARKER = "INTAKE_CANCEL"


def _extract_json_blob(text: str) -> Optional[dict]:
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"(\{.*\})", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
    return None


def _format_transcript(messages: List[Dict[str, str]]) -> str:
    lines = []
    for m in messages:
        role = m.get("role", "user")
        label = "Пользователь" if role == "user" else "Наставник"
        lines.append(f"{label}: {m.get('content', '')}")
    return "\n".join(lines)


def _transcript_to_chat_messages(
    transcript: List[Dict[str, str]], *, system_prompt: str
) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for m in transcript:
        role = m.get("role", "user")
        if role not in ("user", "assistant"):
            role = "user"
        content = (m.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content})
    return messages


class ScriptureChallengeService:
    def __init__(self, user_storage):
        self.user_storage = user_storage
        self.agents = AgentsClient(user_storage)

    async def intake_reply(
        self, *, user_id: int, transcript: List[Dict[str, str]]
    ) -> Tuple[str, Optional[str], bool]:
        """
        Returns: (assistant_text, summary_if_ready, canceled)
        """
        messages = _transcript_to_chat_messages(transcript, system_prompt=INTAKE_SYSTEM_PROMPT)
        if len(messages) < 2:
            logger.warning(
                "intake_reply empty transcript user=%s messages=%s",
                user_id,
                len(messages),
            )

        raw = await self.agents.complete_with_messages(
            messages=messages,
            user_id=user_id,
            request_kind="scripture_challenge_intake",
            temperature=0.5,
            max_tokens=1200,
        )
        if not raw:
            return ("Не удалось обработать сообщение. Попробуйте ещё раз.", None, False)

        if INTAKE_CANCEL_MARKER in raw:
            return ("", None, True)

        if INTAKE_READY_MARKER in raw:
            parts = raw.split(INTAKE_READY_MARKER, 1)
            visible = parts[0].strip()
            payload = _extract_json_blob(parts[1] if len(parts) > 1 else "")
            summary = (payload or {}).get("summary") if payload else None
            if not summary:
                summary = visible or (transcript[-1].get("content") if transcript else "")
            return (visible or "Спасибо, я понял ваш запрос. Перейдём к настройке челленджа.", summary, False)

        return (raw.strip(), None, False)

    async def build_plan_with_review(
        self,
        *,
        user_id: int,
        summary: str,
        duration_days: int,
        intake_transcript: List[Dict[str, str]],
    ) -> Optional[List[Dict[str, Any]]]:
        base_user = (
            f"Запрос пользователя (резюме):\n{summary}\n\n"
            f"Срок челленджа: {duration_days} дней.\n\n"
            f"Диалог:\n{_format_transcript(intake_transcript)}"
        )
        plan_items: Optional[List[Dict[str, Any]]] = None
        feedback: Optional[str] = None

        for round_n in range(MAX_PLAN_REVISION_ROUNDS):
            planner_input = base_user
            if feedback:
                planner_input += f"\n\nПравки от редактора:\n{feedback}"

            planner_raw = await self.agents.complete(
                system_prompt=PLANNER_SYSTEM_PROMPT,
                user_content=planner_input,
                user_id=user_id,
                request_kind="scripture_challenge_plan",
                temperature=0.4,
                max_tokens=8000,
            )
            plan_data = _extract_json_blob(planner_raw or "")
            if not plan_data or "items" not in plan_data:
                logger.warning("planner round %s: invalid JSON", round_n)
                continue
            plan_items = plan_data["items"]

            review_raw = await self.agents.complete(
                system_prompt=REVIEWER_SYSTEM_PROMPT,
                user_content=json.dumps(
                    {"user_summary": summary, "duration_days": duration_days, "plan": plan_items},
                    ensure_ascii=False,
                ),
                user_id=user_id,
                request_kind="scripture_challenge_plan_review",
                temperature=0.2,
                max_tokens=8000,
            )
            review = _extract_json_blob(review_raw or "") or {}
            if review.get("approved"):
                return plan_items

            if review.get("revised_plan") and review["revised_plan"].get("items"):
                plan_items = review["revised_plan"]["items"]
                # second review on revised
                review2_raw = await self.agents.complete(
                    system_prompt=REVIEWER_SYSTEM_PROMPT,
                    user_content=json.dumps(
                        {"user_summary": summary, "duration_days": duration_days, "plan": plan_items},
                        ensure_ascii=False,
                    ),
                    user_id=user_id,
                    request_kind="scripture_challenge_plan_review_final",
                    temperature=0.2,
                    max_tokens=8000,
                )
                review2 = _extract_json_blob(review2_raw or "") or {}
                if review2.get("approved"):
                    return plan_items

            feedback = review.get("feedback") or "Улучши план: логика, полнота стихов, соответствие запросу."

        return plan_items

    async def daily_comment(
        self,
        *,
        user_id: int,
        summary: str,
        plan_excerpt: str,
        recent_dialog: str,
        today_reference: str,
        today_passage: str,
    ) -> Optional[str]:
        user_content = (
            f"Запрос: {summary}\n\nПлан (фрагмент): {plan_excerpt}\n\n"
            f"Диалог челленджа (недавно):\n{recent_dialog}\n\n"
            f"Сегодня: {today_reference}\n{today_passage}"
        )
        return await self.agents.complete(
            system_prompt=DAILY_COMMENT_SYSTEM_PROMPT,
            user_content=user_content,
            user_id=user_id,
            request_kind="scripture_challenge_daily",
            temperature=0.6,
            max_tokens=800,
        )

    async def challenge_chat_reply(
        self,
        *,
        user_id: int,
        summary: str,
        plan_summary: str,
        current_day: int,
        recent_messages: List[Dict[str, Any]],
    ) -> Optional[str]:
        system = (
            f"{CHALLENGE_CHAT_SYSTEM_PROMPT}\n\n"
            f"Первоначальный запрос: {summary}\n"
            f"Текущий день челленджа: {current_day}\n"
            f"План чтения: {plan_summary}"
        )
        transcript = [
            {"role": m["role"], "content": m["content"]}
            for m in recent_messages
            if (m.get("content") or "").strip()
        ]
        messages = _transcript_to_chat_messages(transcript, system_prompt=system)
        if len(messages) < 2:
            return None
        return await self.agents.complete_with_messages(
            messages=messages,
            user_id=user_id,
            request_kind="scripture_challenge_chat",
            temperature=0.65,
            max_tokens=1200,
        )

    async def weekly_review_plan(
        self,
        *,
        user_id: int,
        summary: str,
        current_day: int,
        duration_days: int,
        plan_items: List[Dict[str, Any]],
        recent_messages: List[Dict[str, Any]],
    ) -> Optional[List[Dict[str, Any]]]:
        dialog = _format_transcript(
            [{"role": m["role"], "content": m["content"]} for m in recent_messages]
        )
        pending = [it for it in plan_items if int(it["day_number"]) >= current_day]
        user_content = json.dumps(
            {
                "summary": summary,
                "current_day": current_day,
                "duration_days": duration_days,
                "pending_plan": pending,
                "recent_dialog": dialog,
            },
            ensure_ascii=False,
        )

        for _ in range(MAX_WEEKLY_REVISION_ROUNDS):
            raw = await self.agents.complete(
                system_prompt=WEEKLY_REVIEW_SYSTEM_PROMPT,
                user_content=user_content,
                user_id=user_id,
                request_kind="scripture_challenge_weekly",
                temperature=0.35,
                max_tokens=6000,
            )
            data = _extract_json_blob(raw or "")
            if not data or not data.get("needs_plan_update"):
                return None
            updates = data.get("updated_items")
            if not updates:
                return None

            ctrl = await self.agents.complete(
                system_prompt=WEEKLY_REVIEW_CONTROLLER_PROMPT,
                user_content=json.dumps({"updates": updates, "summary": summary}, ensure_ascii=False),
                user_id=user_id,
                request_kind="scripture_challenge_weekly_review",
                temperature=0.2,
                max_tokens=1500,
            )
            ctrl_data = _extract_json_blob(ctrl or "") or {}
            if ctrl_data.get("approved"):
                return updates
        return None
