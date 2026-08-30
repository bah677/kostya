"""Черновик ответа службы поддержки через DeepSeek."""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from config import config

logger = logging.getLogger(__name__)

_SUPPORT_DRAFT_SYSTEM = """Ты помощник службы поддержки Telegram-бота «Ответ из Библии».
Составь черновик ответа пользователю от лица живой поддержки (не от лица бота-собеседника).

Требования:
- 3–8 предложений, по-русски, без HTML и Markdown;
- спокойный человечный тон, без канцелярита;
- если проблема техническая — конкретный шаг (повторить сообщение, /start, подождать минуту);
- если обращение эмоциональное — мягкое сочувствие без давления и без обещаний исцеления;
- не пиши, что ты ИИ; не проси донат;
- не выдумывай факты о сбоях: опирайся только на контекст ниже."""

_REQUEST_KIND = "support_ticket_draft"


async def generate_support_ticket_draft(
    user_storage: Any,
    *,
    user_id: int,
    ticket_number: str,
    topic: str,
    user_message: str,
) -> Optional[str]:
    if not getattr(config, "SUPPORT_AI_DRAFT_ENABLED", True):
        return None
    try:
        from openai_client.agents_client import AgentsClient

        agents = AgentsClient(user_storage)
    except Exception as e:
        logger.warning("support_ai_draft: AgentsClient unavailable: %s", e)
        return None

    history_lines: List[str] = []
    try:
        rows = await user_storage.get_private_chat_history(user_id, limit=12)
        for row in rows[-8:]:
            role = (row.get("role") or "user").strip()
            content = (row.get("content") or "").strip()
            if not content:
                continue
            label = "Пользователь" if role == "user" else "Бот"
            history_lines.append(f"{label}: {content[:400]}")
    except Exception as e:
        logger.debug("support_ai_draft history uid=%s: %s", user_id, e)

    incidents: List[str] = []
    if hasattr(user_storage, "ensure_prayer_tech_incidents_schema"):
        try:
            async with user_storage.get_connection() as conn:
                inc_rows = await conn.fetch(
                    """
                    SELECT kind, detail, created_at
                    FROM prayer_tech_incidents
                    WHERE user_id = $1
                      AND created_at > NOW() - interval '24 hours'
                    ORDER BY created_at DESC
                    LIMIT 5
                    """,
                    int(user_id),
                )
                for r in inc_rows:
                    d = (r.get("detail") or r.get("kind") or "").strip()
                    if d:
                        incidents.append(d[:200])
        except Exception as e:
            logger.debug("support_ai_draft incidents uid=%s: %s", user_id, e)

    parts = [
        f"Тикет: {ticket_number}",
        f"Тема: {topic}",
        f"Обращение пользователя:\n{user_message.strip()}",
    ]
    if history_lines:
        parts.append("Недавний диалог с ботом:\n" + "\n".join(history_lines))
    if incidents:
        parts.append(
            "Зафиксированные тех. сбои за 24ч:\n" + "\n".join(f"· {x}" for x in incidents)
        )
    parts.append(
        "Напиши только текст ответа пользователю — без преамбулы и без подписи."
    )
    user_content = "\n\n".join(parts)

    try:
        text = await agents.complete(
            system_prompt=_SUPPORT_DRAFT_SYSTEM,
            user_content=user_content,
            user_id=int(user_id),
            request_kind=_REQUEST_KIND,
            temperature=0.35,
            max_tokens=700,
        )
        draft = (text or "").strip()
        if len(draft) < 20:
            logger.warning(
                "support_ai_draft: too short ticket=%s chars=%s",
                ticket_number,
                len(draft),
            )
            return None
        return draft[:3500]
    except Exception as e:
        logger.error("support_ai_draft failed ticket=%s: %s", ticket_number, e)
        return None
