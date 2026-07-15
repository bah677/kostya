"""Верификация черновиков ответов member-агента."""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, TYPE_CHECKING

from openai import AsyncOpenAI

from bot.services.agent_datetime_context import prepend_datetime_context
from bot.texts.prompts.member_agent_verifier import MEMBER_VERIFIER_SYSTEM
from rag.source_links import extract_any_tme_links, extract_public_links_from_text, is_public_member_link
from storage.db.llm_token_normalize import extract_token_counts_and_extras

if TYPE_CHECKING:
    from storage.database import Database

logger = logging.getLogger(__name__)

CHAT_MODEL = "deepseek-chat"

_TME_LINK_RE = re.compile(r"https://t\.me/[^\s\]<>\")']+", re.IGNORECASE)
_BARE_TME_RE = re.compile(r"(?<![/\w])t\.me/[^\s\]<>\")']+", re.IGNORECASE)


@dataclass(frozen=True)
class VerifierResult:
    ok: bool
    issues: List[str]
    severity: str = "none"


def extract_allowed_links_from_context(*parts: Optional[str]) -> List[str]:
    """Публичные ссылки из RAG/контекста, которые можно цитировать в ответе."""
    return extract_public_links_from_text(*parts)


def extract_links_from_draft(draft: str) -> List[str]:
    if not draft:
        return []
    out: set[str] = set()
    for m in _TME_LINK_RE.findall(draft):
        out.add(m.rstrip(".,;:)"))
    for m in _BARE_TME_RE.findall(draft):
        url = m if m.startswith("http") else f"https://{m}"
        out.add(url.rstrip(".,;:)"))
    return sorted(out)


def _strip_json_fence(raw: str) -> str:
    t = (raw or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def _parse_verifier_json(raw: str) -> VerifierResult:
    data = json.loads(_strip_json_fence(raw))
    ok = bool(data.get("ok"))
    issues_raw = data.get("issues") or []
    issues = [str(x).strip() for x in issues_raw if str(x).strip()]
    severity = str(data.get("severity") or ("none" if ok else "block")).strip().lower()
    if not ok and severity not in ("block", "minor"):
        severity = "block"
    return VerifierResult(ok=ok, issues=issues, severity=severity)


def _heuristic_block(draft: str, allowed_links: Sequence[str]) -> Optional[VerifierResult]:
    """Быстрые проверки без LLM."""
    if not draft or not draft.strip():
        return VerifierResult(ok=False, issues=["пустой ответ"], severity="block")
    low = draft.lower()
    if "<<<cta_subscribe>>>" in low:
        return VerifierResult(
            ok=False,
            issues=["найден маркер CTA_SUBSCRIBE — участник уже в клубе"],
            severity="block",
        )
    sales_markers = (
        "оформи подписк",
        "купи подписк",
        "вступи в клуб",
        "вступить в клуб",
        "нажми кнопку оплаты",
        "оформить доступ",
    )
    for phrase in sales_markers:
        if phrase in low:
            return VerifierResult(
                ok=False,
                issues=[f"продажный призыв: «{phrase}»"],
                severity="block",
            )
    allowed = set(allowed_links)
    for link in extract_links_from_draft(draft):
        if not is_public_member_link(link):
            return VerifierResult(
                ok=False,
                issues=[f"запрещённая ссылка (не публичная): {link}"],
                severity="block",
            )
        if allowed and link not in allowed:
            return VerifierResult(
                ok=False,
                issues=[f"ссылка не из разрешённого списка: {link}"],
                severity="block",
            )
    if not allowed:
        for link in extract_any_tme_links(draft):
            return VerifierResult(
                ok=False,
                issues=[f"ссылка t.me без разрешения в контексте: {link}"],
                severity="block",
            )
    return None


async def _log_llm(
    user_storage: Optional["Database"],
    *,
    user_id: int,
    kind: str,
    usage: Any,
) -> None:
    if user_storage is None:
        return
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
            "provider": "deepseek",
            "request_id": request_id,
            "model": CHAT_MODEL,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": tt,
        },
        source="deepseek",
        outcome="success",
    )


async def verify_member_agent_draft(
    client: AsyncOpenAI,
    *,
    draft: str,
    user_message: str,
    verification_context: str,
    allowed_links: Sequence[str],
    user_id: int,
    user_storage: Optional["Database"] = None,
) -> VerifierResult:
    """LLM-верификатор + эвристики."""
    heuristic = _heuristic_block(draft, allowed_links)
    if heuristic is not None:
        return heuristic

    links_block = (
        "\n".join(f"- {u}" for u in allowed_links)
        if allowed_links
        else "(нет — не добавляй ссылки t.me в ответ)"
    )
    user_block = (
        f"ВОПРОС УЧАСТНИКА:\n{user_message[:4000]}\n\n"
        f"КОНТЕКСТ ДЛЯ ПРОВЕРКИ (фрагменты RAG и описание):\n"
        f"{(verification_context or '')[:12000]}\n\n"
        f"РАЗРЕШЁННЫЕ ССЫЛКИ:\n{links_block}\n\n"
        f"ЧЕРНОВИК ОТВЕТА:\n{draft[:6000]}"
    )

    try:
        resp = await client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": prepend_datetime_context(MEMBER_VERIFIER_SYSTEM)},
                {"role": "user", "content": user_block},
            ],
            temperature=0.1,
            max_tokens=800,
        )
        await _log_llm(
            user_storage,
            user_id=user_id,
            kind="member_agent_verifier",
            usage=getattr(resp, "usage", None),
        )
        raw = resp.choices[0].message.content or ""
        result = _parse_verifier_json(raw)
        if not result.ok and result.severity == "block":
            logger.info(
                "member verifier blocked user=%s issues=%s",
                user_id,
                result.issues,
            )
        return result
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning("member verifier parse error user=%s: %s", user_id, e)
        return VerifierResult(
            ok=False,
            issues=["верификатор не смог разобрать ответ"],
            severity="block",
        )
    except Exception as e:
        logger.error("member verifier failed user=%s: %s", user_id, e)
        return VerifierResult(ok=True, issues=[], severity="none")
