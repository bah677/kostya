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
- не выдумывай факты; не пиши, что ты ИИ; не обещай бесплатный доступ от поддержки без оснований.

ПУТИ В БОТЕ — ТОЛЬКО ИЗ СПРАВКИ.
Ниже в запросе есть блок «Справка по боту», собранный из кода. Названия пунктов
меню там точные. Если нужного пути в справке нет — значит, в боте его нет.
Не придумывай название пункта и не отправляй человека искать его: напиши, что
уточнишь и вернёшься. Неверный путь хуже, чем честное «сейчас уточню»:
человек уже один раз не нашёл нужное, второй промах он воспримет как отписку.

ПРИЧИНА ВАЖНЕЕ РИТУАЛА.
Если в контексте (логи, ошибки бота, история) видно причину — назови её и
следующий шаг по ней.
Если причина неизвестна — так и напиши. НЕ предлагай «отправьте /start,
подождите 10–15 минут и повторите» просто чтобы чем-то заполнить ответ: это
читается как отписка и почти никогда не помогает. Перезапуск уместен, только
когда из контекста видно, что проблема в зависшем состоянии диалога.

НЕ СПРАШИВАЙ ТО, ЧТО УЖЕ ЗНАЕШЬ.
Если номер заказа, сумма, время попытки или текст ошибки есть в контексте —
используй их. Просить у человека данные, которые уже лежат в логах, нельзя.

ЧТО ДЕЛАЕТ ПОДДЕРЖКА, А ЧТО ЧЕЛОВЕК.
Сам человек может: повторить оплату, открыть пункт меню, прислать скриншот.
Он НЕ может: расчистить зависшую запись, сверить заказ в платёжной системе,
перенести оплату на другого участника, продлить доступ руками. Это делает
поддержка. Не поручай человеку то, что должны сделать мы, — особенно если он
уже написал, что у него не получается.

ПРО ДЕНЬГИ.
Если оплата не прошла и причина на стороне банка или платёжной системы —
не отправляй человека «попробовать ещё раз той же картой». Скажи, что
разбираемся с заказом, и предложи рабочую альтернативу, если она есть в справке.

ЕСЛИ ЕСТЬ БЛОК «ОДОБРЕННЫЕ ОТВЕТЫ».
Это ответы, которые админ уже отправлял по похожим обращениям. Держись их:
формулировки и факты оттуда вернее твоих догадок.

ЕСЛИ ЕСТЬ БЛОК «ЗАМЕЧАНИЯ АДМИНОВ».
Это правки, которые админы вносили в прошлые черновики. Учитывай их сразу,
чтобы одно и то же не приходилось диктовать снова.

Если у человека финансовые трудности и он хочет остаться — упомяни Доску
добрых дел (точное название пункта возьми из справки): там можно попросить
о продлении подписки."""


_REVISION_SYSTEM = """Ты помощник службы поддержки клубного бота. Перед тобой
черновик ответа и правка от администратора.

Перепиши черновик с учётом правки. Правка администратора — главнее всего
остального: если она противоречит твоему прошлому тексту, прав администратор.

- меняй только то, что просят, остальное сохрани;
- 4–8 предложений, по-русски, без HTML и Markdown;
- пути в боте бери только из блока «Справка по боту»;
- не пиши, что ты ИИ, не извиняйся за прошлый вариант и не комментируй правку;
- верни ТОЛЬКО новый текст ответа пользователю."""


_REQUEST_KIND = "support_ticket_draft"
_REVISION_REQUEST_KIND = "support_ticket_draft_revise"

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
    # Выдуманный путь в меню — худший дефект черновика: человек уже один раз
    # не нашёл нужное, второй промах он воспримет как отписку.
    try:
        from bot.services.support_bot_reference import find_invented_menu_paths

        invented = find_invented_menu_paths(d)
        if invented:
            logger.info("support_ai_draft: выдуманные пути %s", invented[:3])
            return False
    except Exception as e:
        logger.debug("support_ai_draft path check: %s", e)
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

    # Справка собирается из констант меню и каталога команд. Руками её вести
    # нельзя: прошлая версия разошлась с ботом и уводила людей к
    # несуществующим пунктам.
    try:
        from bot.services.support_bot_reference import build_bot_reference

        reference = build_bot_reference()
    except Exception as e:
        logger.warning("support_ai_draft reference: %s", e)
        reference = ""
    if reference:
        parts.append(reference)

    # Ответы, которые админ уже отправлял по похожим обращениям.
    try:
        faq = await user_storage.find_support_faq(user_message)
    except Exception as e:
        logger.debug("support_ai_draft faq: %s", e)
        faq = []
    if faq:
        faq_lines = []
        for item in faq:
            q = _strip_html(str(item.get("question") or ""))[:300]
            a = _strip_html(str(item.get("answer") or ""))[:700]
            faq_lines.append(f"· Вопрос: {q}\n  Ответ админа: {a}")
        parts.append(
            "ОДОБРЕННЫЕ ОТВЕТЫ по похожим обращениям "
            "(факты и формулировки отсюда вернее догадок):\n"
            + "\n".join(faq_lines)
        )

    # Повторяющиеся замечания админов к прошлым черновикам.
    try:
        notes = await user_storage.recent_support_draft_instructions(limit=10)
    except Exception as e:
        logger.debug("support_ai_draft notes: %s", e)
        notes = []
    if notes:
        parts.append(
            "ЗАМЕЧАНИЯ АДМИНОВ к прошлым черновикам (учти сразу):\n"
            + "\n".join(f"· {n[:300]}" for n in notes)
        )
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
        # deepseek-v4-flash тратит бюджет на reasoning_tokens; при max_tokens=900
        # content часто пустой (finish_reason=length), черновик ТП не появляется.
        text = await agents.complete(
            system_prompt=_SUPPORT_DRAFT_SYSTEM,
            user_content=user_content,
            user_id=int(user_id),
            request_kind=_REQUEST_KIND,
            temperature=0.3,
            max_tokens=4000,
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


async def revise_support_ticket_draft(
    user_storage: Any,
    *,
    user_id: int,
    ticket_number: str,
    user_message: str,
    current_draft: str,
    instruction: str,
) -> Optional[str]:
    """Переписывает черновик по замечанию админа.

    Петля обратной связи: админ отвечает на сообщение с черновиком, говорит,
    что не так, и модель правит. Предыдущие раунды по этому же тикету тоже
    уходят в запрос — иначе вторая правка откатывает первую.

    Сами замечания копятся в support_draft_revisions и дальше подаются во все
    новые черновики (см. recent_support_draft_instructions): повторяющуюся
    претензию не придётся диктовать каждый раз.
    """
    instruction = (instruction or "").strip()
    current_draft = (current_draft or "").strip()
    if not instruction or not current_draft:
        return None

    try:
        from openai_client.agents_client import AgentsClient

        agents = AgentsClient(user_storage, system_prompt_override="")
    except Exception as e:
        logger.warning("support_draft_revise: AgentsClient unavailable: %s", e)
        return None

    parts = [
        f"Тикет: {ticket_number}",
        f"Обращение пользователя:\n{(user_message or '').strip()}",
    ]
    try:
        from bot.services.support_bot_reference import build_bot_reference

        reference = build_bot_reference()
        if reference:
            parts.append(reference)
    except Exception as e:
        logger.debug("support_draft_revise reference: %s", e)

    try:
        prior = await user_storage.get_support_draft_revisions(ticket_number)
    except Exception as e:
        logger.debug("support_draft_revise prior: %s", e)
        prior = []
    if prior:
        parts.append(
            "Что уже просили поправить в этом тикете (не откатывай):\n"
            + "\n".join(
                f"· раунд {r.get('round')}: {str(r.get('instruction') or '')[:300]}"
                for r in prior
            )
        )

    parts.append(f"Текущий черновик:\n{current_draft}")
    parts.append(f"ПРАВКА АДМИНИСТРАТОРА (главнее всего):\n{instruction}")

    try:
        text = await agents.complete(
            system_prompt=_REVISION_SYSTEM,
            user_content="\n\n".join(parts),
            user_id=int(user_id),
            request_kind=_REVISION_REQUEST_KIND,
            temperature=0.3,
            max_tokens=4000,
        )
    except Exception as e:
        logger.error("support_draft_revise failed ticket=%s: %s", ticket_number, e)
        return None

    revised = (text or "").strip()
    if len(revised) < _MIN_DRAFT_CHARS:
        logger.warning(
            "support_draft_revise: слишком короткий ответ ticket=%s chars=%s",
            ticket_number,
            len(revised),
        )
        return None
    # Выдуманные пути отсекаем и здесь: правка не повод их пропустить.
    try:
        from bot.services.support_bot_reference import find_invented_menu_paths

        invented = find_invented_menu_paths(revised)
        if invented:
            logger.info(
                "support_draft_revise: выдуманные пути %s ticket=%s",
                invented[:3],
                ticket_number,
            )
    except Exception:
        pass
    return revised[:3500]
