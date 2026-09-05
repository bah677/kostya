"""Черновик ответа службы поддержки через DeepSeek."""

from __future__ import annotations

import html as html_module
import logging
import re
from datetime import datetime
from typing import Any, List, Optional, Set

from config import config

logger = logging.getLogger(__name__)

_MIN_DRAFT_CHARS = 80
_HISTORY_MESSAGES = 10
_HISTORY_SNIPPET = 500

_SUPPORT_DRAFT_SYSTEM = """Ты помощник службы поддержки Telegram-бота «Ответ из Библии».
Составь черновик ответа пользователю от лица живой поддержки (не от лица бота-собеседника).

Жёсткие правила:
- 4–8 предложений, по-русски, без HTML и Markdown;
- ОБЯЗАТЕЛЬНО ответь на конкретное обращение из блока «Обращение пользователя» — процитируй или переформулируй его боль/вопрос;
- запрещены пустые фразы: «мы ценим ваше обращение», «спасибо за терпение», «мы всегда рады помочь» без конкретики;
- если проблема техническая — назови причину из контекста (если есть) и один-два шага: повторить сообщение, /start, подождать 1–2 мин;
- если обращение эмоциональное — одно предложение сочувствия, дальше по делу;
- не выдумывай сбои и факты, которых нет в контексте;
- не пиши, что ты ИИ; не проси донат; не обещай исцеления.

Справка по боту (только если уместно по обращению):
- «Что-то пошло не так» — временный сбой генерации или короткий рестарт; обычно помогает отправить сообщение ещё раз;
- молитва: /prayer или кнопка «Помолиться», нужно написать хотя бы одну строку после нажатия;
- поддержка: /support — тикет, ответ в течение суток."""

_REQUEST_KIND = "support_ticket_draft"

_GENERIC_BANNED = (
    "мы ценим ваше обращение",
    "благодарим за обращение",
    "спасибо за ваше терпение",
    "мы всегда рады помочь",
    "обращайтесь в любое время",
    "надеемся на ваше понимание",
)


def _strip_html(text: str) -> str:
    t = re.sub(r"<[^>]+>", " ", text or "")
    t = html_module.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def _keywords(text: str, *, min_len: int = 4) -> Set[str]:
    words = re.findall(r"[а-яёa-z]{%d,}" % min_len, (text or "").casefold())
    stop = {
        "бот",
        "бота",
        "боте",
        "ботом",
        "этот",
        "этого",
        "когда",
        "сейчас",
        "очень",
        "просто",
        "можно",
        "надо",
        "если",
        "чтобы",
        "потому",
        "тогда",
        "тоже",
        "ещё",
        "уже",
        "все",
        "всё",
        "меня",
        "мне",
        "меня",
        "написал",
        "написала",
        "сообщение",
        "пишет",
        "работает",
        "работать",
    }
    return {w for w in words if w not in stop}


def _draft_quality_ok(draft: str, user_message: str) -> bool:
    d = (draft or "").strip()
    if len(d) < _MIN_DRAFT_CHARS:
        return False
    low = d.casefold()
    if any(b in low for b in _GENERIC_BANNED):
        return False
    keys = _keywords(user_message)
    if not keys:
        return True
    dkeys = _keywords(d)
    if keys & dkeys:
        return True
    # короткое обращение — достаточно длины без generic
    return len(user_message.strip()) < 25 and len(d) >= _MIN_DRAFT_CHARS


async def _fetch_recent_bot_errors(
    user_storage: Any, user_id: int
) -> List[str]:
    """Последние «сбойные» исходящие сообщения бота пользователю."""
    patterns = (
        "%пошло не так%",
        "%не удалось%",
        "%временно недоступен%",
        "%ошибка%",
        "%попробуйте ещё раз%",
        "%попробуйте еще раз%",
    )
    try:
        async with user_storage.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT content, created_at
                FROM messages
                WHERE user_id = $1
                  AND chat_type = 'private'
                  AND sender_type = 'bot'
                  AND created_at > NOW() - interval '24 hours'
                  AND (
                    content ILIKE ANY($2::text[])
                  )
                ORDER BY created_at DESC
                LIMIT 5
                """,
                int(user_id),
                list(patterns),
            )
        out: List[str] = []
        for r in rows:
            plain = _strip_html(str(r.get("content") or ""))[:220]
            if plain:
                out.append(plain)
        return out
    except Exception as e:
        logger.debug("support_ai_draft bot errors uid=%s: %s", user_id, e)
        return []


async def _fetch_prior_tickets(
    user_storage: Any, user_id: int, *, exclude: str
) -> List[str]:
    try:
        if not hasattr(user_storage, "get_user_tickets"):
            return []
        rows = await user_storage.get_user_tickets(user_id, limit=3)
        out: List[str] = []
        for r in rows:
            tn = str(r.get("ticket_number") or "")
            if tn.upper() == (exclude or "").upper():
                continue
            msg = _strip_html(str(r.get("user_message") or ""))[:180]
            st = str(r.get("status") or "")
            if msg:
                out.append(f"{tn} ({st}): {msg}")
        return out
    except Exception as e:
        logger.debug("support_ai_draft prior tickets uid=%s: %s", user_id, e)
        return []


async def generate_support_ticket_draft(
    user_storage: Any,
    *,
    user_id: int,
    ticket_number: str,
    topic: str,
    user_message: str,
    ticket_created_at: Optional[datetime] = None,
) -> Optional[str]:
    if not getattr(config, "SUPPORT_AI_DRAFT_ENABLED", True):
        return None
    try:
        from openai_client.agents_client import AgentsClient

        agents = AgentsClient(user_storage)
    except Exception as e:
        logger.warning("support_ai_draft: AgentsClient unavailable: %s", e)
        return None

    user_message = (user_message or "").strip()
    history_lines: List[str] = []
    try:
        rows = await user_storage.get_private_chat_history(
            user_id, limit=_HISTORY_MESSAGES + 4
        )
        for row in rows[-_HISTORY_MESSAGES:]:
            role = (row.get("role") or "user").strip()
            content = _strip_html(row.get("content") or "")
            if not content:
                continue
            label = "Пользователь" if role == "user" else "Бот"
            history_lines.append(f"{label}: {content[:_HISTORY_SNIPPET]}")
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
                    kind = (r.get("kind") or "").strip()
                    detail = (r.get("detail") or "").strip()
                    d = f"{kind}: {detail}" if detail else kind
                    if d:
                        incidents.append(d[:200])
        except Exception as e:
            logger.debug("support_ai_draft incidents uid=%s: %s", user_id, e)

    bot_errors = await _fetch_recent_bot_errors(user_storage, user_id)
    prior_tickets = await _fetch_prior_tickets(
        user_storage, user_id, exclude=ticket_number
    )

    log_excerpt: List[str] = []
    try:
        from bot.services.support_log_excerpt import fetch_user_log_excerpt

        log_excerpt = await fetch_user_log_excerpt(
            user_id,
            anchor_at=ticket_created_at,
            configured_paths=str(
                getattr(config, "SUPPORT_DRAFT_LOG_PATHS", "") or ""
            ),
            window_before_min=int(
                getattr(config, "SUPPORT_DRAFT_LOG_WINDOW_MIN", 120) or 120
            ),
            tail_bytes=int(
                getattr(config, "SUPPORT_DRAFT_LOG_TAIL_BYTES", 3_000_000) or 3_000_000
            ),
        )
    except Exception as e:
        logger.debug("support_ai_draft log excerpt uid=%s: %s", user_id, e)

    parts = [
        f"Тикет: {ticket_number}",
        f"Тема: {topic}",
        f"Обращение пользователя (главное — ответь на это):\n{user_message}",
    ]
    if history_lines:
        parts.append(
            "Недавний диалог с ботом (для контекста, не пересказывай целиком):\n"
            + "\n".join(history_lines)
        )
    if bot_errors:
        parts.append(
            "Сообщения бота об ошибке за 24ч:\n"
            + "\n".join(f"· {x}" for x in bot_errors)
        )
    if incidents:
        parts.append(
            "Зафиксированные тех. инциденты за 24ч:\n"
            + "\n".join(f"· {x}" for x in incidents)
        )
    if prior_tickets:
        parts.append(
            "Прошлые обращения этого пользователя:\n"
            + "\n".join(f"· {x}" for x in prior_tickets)
        )
    if log_excerpt:
        parts.append(
            "Выдержка prod-логов бота по этому user_id "
            f"(±{getattr(config, 'SUPPORT_DRAFT_LOG_WINDOW_MIN', 120)} мин от тикета):\n"
            + "\n".join(log_excerpt)
        )

    base_user = "\n\n".join(parts)

    async def _call(extra: str = "") -> Optional[str]:
        user_content = base_user
        if extra:
            user_content += f"\n\n{extra}"
        user_content += (
            "\n\nНапиши только текст ответа пользователю — без преамбулы и без подписи."
        )
        # deepseek-v4-flash тратит бюджет на reasoning_tokens; при max_tokens=900
        # content часто пустой. Отключаем thinking + поднимаем лимит.
        text = await agents.complete(
            system_prompt=_SUPPORT_DRAFT_SYSTEM,
            user_content=user_content,
            user_id=int(user_id),
            request_kind=_REQUEST_KIND,
            temperature=0.3,
            max_tokens=4000,
            thinking="disabled",
        )
        return (text or "").strip()

    try:
        draft = await _call()
        if draft and not _draft_quality_ok(draft, user_message):
            logger.info(
                "support_ai_draft: quality retry ticket=%s keys=%s",
                ticket_number,
                sorted(_keywords(user_message))[:6],
            )
            draft = await _call(
                extra=(
                    "Предыдущий черновик был слишком общим. "
                    f"Начни с ответа на фразу пользователя: «{user_message[:200]}». "
                    "Не используй шаблонные благодарности."
                )
            )
        if not draft or not _draft_quality_ok(draft, user_message):
            logger.warning(
                "support_ai_draft: rejected ticket=%s chars=%s",
                ticket_number,
                len(draft or ""),
            )
            return None
        return draft[:3500]
    except Exception as e:
        logger.error("support_ai_draft failed ticket=%s: %s", ticket_number, e)
        return None
