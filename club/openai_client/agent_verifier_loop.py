"""Цикл генерация → верификация → повтор для member-агента."""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

from openai_client.member_agent_verifier import verify_member_agent_draft

logger = logging.getLogger(__name__)


async def run_with_verifier_retries(
    *,
    generate: Callable[[str], Awaitable[Optional[str]]],
    verify_kwargs: dict,
    max_retries: int,
    fallback: str,
    user_id: int,
) -> Optional[str]:
    """
    ``generate(extra_system)`` — черновик ответа; ``extra_system`` — доп. блок в system при ретрае.

    ``max_retries`` — число повторов после первого черновика (всего до max_retries + 1 генераций).
    """
    extra = ""
    attempts = max(0, int(max_retries)) + 1
    draft: Optional[str] = None

    for attempt in range(1, attempts + 1):
        draft = await generate(extra)
        if not draft or not str(draft).strip():
            logger.warning(
                "member agent empty draft user=%s attempt=%s",
                user_id,
                attempt,
            )
            extra = (
                "\n\n🔴 Предыдущий ответ пустой. Дай содержательный ответ участнику клуба."
            )
            continue

        result = await verify_member_agent_draft(draft=draft, **verify_kwargs)
        if result.ok:
            if attempt > 1:
                logger.info(
                    "member verifier passed after retry user=%s attempt=%s",
                    user_id,
                    attempt,
                )
            return draft

        issues = "; ".join(result.issues) or "неизвестная ошибка"
        logger.info(
            "member verifier reject user=%s attempt=%s issues=%s",
            user_id,
            attempt,
            issues,
        )
        if attempt >= attempts:
            break
        extra = (
            "\n\n🔴 ВЕРИФИКАТОР ОТКЛОНИЛ ПРЕДЫДУЩИЙ ЧЕРНОВИК:\n"
            f"{issues}\n\n"
            "Перепиши ответ: исправь только указанные проблемы. "
            "Не выдумывай факты и ссылки. Участник уже в клубе — без продажи подписки."
        )

    logger.warning(
        "member agent verifier exhausted user=%s — fallback",
        user_id,
    )
    return fallback
