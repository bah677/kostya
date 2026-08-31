"""Черновик ответа службы поддержки клубного бота через DeepSeek."""

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

_SUPPORT_DRAFT_SYSTEM = """Ты помощник службы поддержки Telegram-бота клуба «Разговоры с Богом» (закрытый клуб «Любящие Бога»).
Составь черновик ответа пользователю от лица живой поддержки (не от лица бота-собеседника).

Жёсткие правила:
- 4–8 предложений, по-русски, без HTML и Markdown;
- ОБЯЗАТЕЛЬНО ответь на конкретное обращение из блока «Обращение пользователя»;
- запрещены пустые фразы без конкретики («мы ценим ваше обращение», «спасибо за терпение»);
- если проблема техническая — назови причину из контекста (если есть) и шаги: /start, повторить, подождать;
- если финансовые трудности и хочет остаться — упомяни «Доску добрых дел»: просьба о продлении подписки, /menu → Доска добрых дел;
- не выдумывай факты; не пиши, что ты ИИ; не обещай бесплатный доступ от поддержки без оснований.

Справка по боту (только если уместно):
- /menu — главное меню; /subs — срок подписки; /support — обращение в поддержку;
- оплата подписки: /menu → Оплатить подписку;
- подарок продления другому участнику: /menu → Подарить продление;
- доска добрых дел: просьбы о помощи, в т.ч. продление подписки."""

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
        "бот", "бота", "боте", "этот", "этого", "когда", "сейчас", "очень",
        "просто", "можно", "надо", "если", "чтобы", "потому", "тогда", "тоже",
        "ещё", "уже", "все", "всё", "меня", "мне", "написал", "написала",
        "сообщение", "пишет", "работает", "работать", "клуб", "клубе",
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
    return len(user_message.strip()) < 25 and len(d) >= _MIN_DRAFT_CHARS


async def _fetch_recent_bot_errors(user_storage: Any, user_id: int) -> List[str]:
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
                  AND content ILIKE ANY($2::text[])
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

        agents = AgentsClient(user_storage, system_prompt_override="")
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
            "Недавний диалог с ботом (для контекста):\n"
            + "\n".join(history_lines)
        )
    if bot_errors:
        parts.append(
            "Сообщения бота об ошибке за 24ч:\n"
            + "\n".join(f"· {x}" for x in bot_errors)
        )
    if prior_tickets:
        parts.append(
            "Прошлые обращения:\n" + "\n".join(f"· {x}" for x in prior_tickets)
        )
    if log_excerpt:
        parts.append(
            "Выдержка prod-логов по user_id "
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
        text = await agents.complete(
            system_prompt=_SUPPORT_DRAFT_SYSTEM,
            user_content=user_content,
            user_id=int(user_id),
            request_kind=_REQUEST_KIND,
            temperature=0.3,
            max_tokens=900,
        )
        return (text or "").strip()

    try:
        draft = await _call()
        if draft and not _draft_quality_ok(draft, user_message):
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
