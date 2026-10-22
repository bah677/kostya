"""Mixin: база ответов поддержки и правки черновиков.

Две таблицы, обе наполняются из обычной работы админа:

* ``support_faq`` — пары «обращение → ответ», одобренные админом. Кладутся
  сюда в момент отправки ответа по тикету и дальше подаются в промпт, чтобы
  повторяющиеся вопросы не сочинялись заново.
* ``support_draft_revisions`` — чем админ был недоволен в черновике. Нужны и
  внутри тикета (переписать), и поверх всех тикетов (повторяющиеся замечания
  становятся постоянными указаниями промпта).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SOURCE_DRAFT_SENT = "draft_sent"
SOURCE_ADMIN_REPLY = "admin_reply"
SOURCE_MANUAL = "manual"

_WORD_RE = re.compile(r"[а-яёa-z0-9]{4,}", re.IGNORECASE)


def keywords(text: str, *, limit: int = 12) -> List[str]:
    """Ключевые слова обращения — по ним ищем похожие ответы в FAQ."""
    seen: List[str] = []
    for w in _WORD_RE.findall((text or "").casefold()):
        if w not in seen:
            seen.append(w)
        if len(seen) >= limit:
            break
    return seen


class SupportKnowledgeMixin:
    # ── FAQ ──────────────────────────────────────────────────────────────
    async def add_support_faq_entry(
        self,
        *,
        question: str,
        answer: str,
        ticket_number: Optional[str] = None,
        approved_by: Optional[int] = None,
        source: str = SOURCE_ADMIN_REPLY,
    ) -> bool:
        q = (question or "").strip()
        a = (answer or "").strip()
        # Совсем короткие пары бесполезны: «спасибо» / «ок» в базу не нужны.
        if len(q) < 10 or len(a) < 40:
            return False
        try:
            async with self.get_connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO support_faq
                        (ticket_number, question, answer, approved_by, source)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    ticket_number,
                    q,
                    a,
                    approved_by,
                    source,
                )
            return True
        except Exception as e:
            logger.error("add_support_faq_entry ticket=%s: %s", ticket_number, e)
            return False

    async def find_support_faq(
        self, question: str, *, limit: int = 4
    ) -> List[Dict[str, Any]]:
        """Похожие одобренные ответы.

        Ранжируем по числу совпавших ключевых слов обращения; при равенстве —
        свежие вперёд. Полнотекстовый поиск тут избыточен: записей будут
        десятки, а не тысячи.
        """
        keys = keywords(question)
        if not keys:
            return []
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT question, answer, source, approved_at,
                           (
                             SELECT count(*) FROM unnest($1::text[]) AS k
                             WHERE position(k IN lower(question)) > 0
                           ) AS hits
                    FROM support_faq
                    WHERE is_active = TRUE
                    ORDER BY hits DESC, approved_at DESC
                    LIMIT $2
                    """,
                    keys,
                    int(limit),
                )
            return [dict(r) for r in rows if int(r["hits"] or 0) > 0]
        except Exception as e:
            logger.error("find_support_faq: %s", e)
            return []

    async def support_faq_count(self) -> int:
        try:
            async with self.get_connection() as conn:
                return int(
                    await conn.fetchval(
                        "SELECT count(*) FROM support_faq WHERE is_active = TRUE"
                    )
                    or 0
                )
        except Exception as e:
            logger.error("support_faq_count: %s", e)
            return 0

    # ── Правки черновиков ────────────────────────────────────────────────
    async def add_support_draft_revision(
        self,
        *,
        ticket_number: str,
        instruction: str,
        draft_before: Optional[str] = None,
        draft_after: Optional[str] = None,
        admin_id: Optional[int] = None,
    ) -> int:
        """Записывает правку и возвращает номер раунда."""
        try:
            async with self.get_connection() as conn:
                rnd = int(
                    await conn.fetchval(
                        """
                        SELECT coalesce(max(round), 0) + 1
                        FROM support_draft_revisions WHERE ticket_number = $1
                        """,
                        ticket_number,
                    )
                    or 1
                )
                await conn.execute(
                    """
                    INSERT INTO support_draft_revisions
                        (ticket_number, round, instruction, draft_before,
                         draft_after, admin_id)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    ticket_number,
                    rnd,
                    (instruction or "").strip(),
                    draft_before,
                    draft_after,
                    admin_id,
                )
                return rnd
        except Exception as e:
            logger.error("add_support_draft_revision ticket=%s: %s", ticket_number, e)
            return 0

    async def get_support_draft_revisions(
        self, ticket_number: str
    ) -> List[Dict[str, Any]]:
        """Все правки по тикету — чтобы следующий раунд учёл предыдущие."""
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT round, instruction, draft_after, created_at
                    FROM support_draft_revisions
                    WHERE ticket_number = $1
                    ORDER BY round
                    """,
                    ticket_number,
                )
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("get_support_draft_revisions %s: %s", ticket_number, e)
            return []

    async def recent_support_draft_instructions(
        self, *, limit: int = 12
    ) -> List[str]:
        """Свежие замечания админов по всем тикетам.

        Идут в промпт постоянными указаниями: если одно и то же приходится
        править из раза в раз, модель должна учесть это заранее, а не ждать
        очередной правки.
        """
        try:
            async with self.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT ON (lower(instruction)) instruction, created_at
                    FROM support_draft_revisions
                    WHERE length(instruction) BETWEEN 8 AND 400
                    ORDER BY lower(instruction), created_at DESC
                    """
                )
            items = sorted(rows, key=lambda r: r["created_at"], reverse=True)
            return [str(r["instruction"]).strip() for r in items[: int(limit)]]
        except Exception as e:
            logger.error("recent_support_draft_instructions: %s", e)
            return []

    async def update_ticket_status_only(
        self, ticket_number: str, status: str, *, admin_id: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """Сменить статус тикета, НЕ трогая admin_response.

        Нужно для закрытия без ответа: бывает, что админ уже разобрался с
        человеком в личке, и слать ему дубль из бота не надо. Существующий
        update_ticket_status пишет admin_response и предполагает ответ.
        """
        try:
            async with self.get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE support_tickets
                    SET status = $2,
                        admin_id = coalesce($3, admin_id),
                        updated_at = NOW()
                    WHERE ticket_number = $1
                    RETURNING ticket_number, user_id, status
                    """,
                    ticket_number,
                    status,
                    admin_id,
                )
            return dict(row) if row else None
        except Exception as e:
            logger.error("update_ticket_status_only %s: %s", ticket_number, e)
            return None
