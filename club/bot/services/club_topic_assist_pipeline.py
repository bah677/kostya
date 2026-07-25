"""Батч-пайплайн topic-assist: triage → RAG → ответы."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional
from zoneinfo import ZoneInfo

from bot.services.club_churn_report import load_aboutclub_text
from bot.services.club_outreach_pilot import user_in_pilot_cohort
from bot.services.club_topic_assist_storage import (
    fetch_answered_source_ids,
    fetch_recent_bot_replies,
    fetch_topic_messages,
)
from bot.services.llm_call_logger import logged_deepseek_chat
from bot.services.llm_request_kinds import (
    CLUB_TOPIC_ASSIST_ANSWER,
    CLUB_TOPIC_ASSIST_CLASSIFY,
)
from bot.texts.prompts.club_topic_assist import (
    TOPIC_ASSIST_ANSWER_SYSTEM,
    TOPIC_ASSIST_TRIAGE_SYSTEM,
)
from config import config
from openai_client.rag_search_planner import (
    RagRetrievalSettings,
    retrieve_for_user_message,
)

if TYPE_CHECKING:
    from openai import AsyncOpenAI
    from rag.runtime import RagStack

logger = logging.getLogger(__name__)
MSK = ZoneInfo("Europe/Moscow")

Visibility = Literal["ephemeral", "public"]

# triage использует тот же request_kind classify (в отчётах по токенам)
CLUB_TOPIC_ASSIST_TRIAGE = CLUB_TOPIC_ASSIST_CLASSIFY


@dataclass(frozen=True)
class TriageItem:
    reply_to_message_id: int
    user_id: int
    visibility: Visibility
    question_summary: str
    reason: str


@dataclass(frozen=True)
class BatchAnswer:
    item: TriageItem
    answer: str
    rag_used: bool


def _strip_json(raw: str) -> str:
    t = (raw or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def _who(row: Dict[str, Any]) -> str:
    un = (row.get("username") or "").strip()
    if un:
        return f"@{un}"
    fn = (row.get("first_name") or "").strip()
    if fn:
        return fn
    return f"id{row.get('user_id')}"


def format_transcript(
    rows: List[Dict[str, Any]],
    *,
    candidate_ids: Optional[set] = None,
) -> str:
    lines: List[str] = []
    for r in rows:
        mid = int(r.get("telegram_message_id") or 0)
        uid = int(r.get("user_id") or 0)
        ts = r.get("created_at")
        if hasattr(ts, "astimezone"):
            ts_s = ts.astimezone(MSK).strftime("%H:%M")
        else:
            ts_s = ""
        text = (r.get("content") or "").replace("\n", " ").strip()
        if len(text) > 500:
            text = text[:499] + "…"
        mark = "CANDIDATE" if candidate_ids and mid in candidate_ids else "CTX"
        lines.append(
            f"[{mark} msg_id={mid} user_id={uid} {_who(r)} {ts_s}] {text}"
        )
    return "\n".join(lines)


def format_bot_replies_blob(rows: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for r in rows:
        mid = int(r.get("source_telegram_message_id") or 0)
        vis = r.get("visibility") or "?"
        ans = (r.get("answer_text") or "").replace("\n", " ").strip()[:400]
        q = (r.get("question_excerpt") or "").replace("\n", " ").strip()[:200]
        lines.append(
            f"[BOT reply_to={mid} visibility={vis}] Q:{q} | A:{ans}"
        )
    return "\n".join(lines)


def parse_triage(raw: Optional[str]) -> List[TriageItem]:
    if not raw:
        return []
    try:
        data = json.loads(_strip_json(raw))
    except Exception:
        logger.warning("triage: bad json")
        return []
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out: List[TriageItem] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            mid = int(it.get("reply_to_message_id"))
            uid = int(it.get("user_id"))
        except (TypeError, ValueError):
            continue
        vis = str(it.get("visibility") or "ephemeral").strip().lower()
        if vis not in ("ephemeral", "public"):
            vis = "ephemeral"
        if not config.CLUB_TOPIC_ASSIST_PUBLIC_ENABLED:
            vis = "ephemeral"
        out.append(
            TriageItem(
                reply_to_message_id=mid,
                user_id=uid,
                visibility=vis,  # type: ignore[arg-type]
                question_summary=str(it.get("question_summary") or "")[:300],
                reason=str(it.get("reason") or "")[:300],
            )
        )
    return out


def batch_windows(now: Optional[datetime] = None):
    """
    candidacy: [now - lag - window, now - lag)
    context:   [candidacy_start - extra, candidacy_start)
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    lag = timedelta(minutes=float(config.CLUB_TOPIC_ASSIST_LAG_MINUTES or 2))
    win = timedelta(minutes=float(config.CLUB_TOPIC_ASSIST_WINDOW_MINUTES or 12))
    extra = timedelta(
        minutes=float(config.CLUB_TOPIC_ASSIST_CONTEXT_EXTRA_MINUTES or 20)
    )
    cand_end = now - lag
    cand_start = cand_end - win
    ctx_start = cand_start - extra
    return ctx_start, cand_start, cand_end


async def triage_batch(
    user_storage,
    *,
    api_key: str,
    candidates_blob: str,
    context_blob: str,
    bot_replies_blob: str,
    already_answered: List[int],
) -> List[TriageItem]:
    user_block = (
        f"already_answered message_ids: {already_answered or []}\n\n"
        f"<<<BOT_REPLIES_RECENT>>>\n{bot_replies_blob or '(нет)'}\n<<<END>>>\n\n"
        f"<<<CONTEXT (не выбирать для ответа)>>>\n"
        f"{context_blob or '(пусто)'}\n<<<END>>>\n\n"
        f"<<<CANDIDATES (можно reply_to только отсюда)>>>\n"
        f"{candidates_blob or '(пусто)'}\n<<<END>>>"
    )
    raw, _ = await logged_deepseek_chat(
        user_storage,
        user_id=0,
        request_kind=CLUB_TOPIC_ASSIST_TRIAGE,
        api_key=api_key,
        system=TOPIC_ASSIST_TRIAGE_SYSTEM,
        user=user_block,
        temperature=0.15,
        max_tokens=800,
        timeout_sec=90.0,
        metadata={"feature": "club_topic_assist", "phase": "triage"},
    )
    return parse_triage(raw)


async def compose_one_answer(
    user_storage,
    *,
    user_id: int,
    question: str,
    thread_blob: str,
    api_key: str,
    llm_client: Optional["AsyncOpenAI"] = None,
    rag_stack: Optional["RagStack"] = None,
) -> tuple[str, bool]:
    about = load_aboutclub_text()
    expert, golden, rag_used = about, "", False
    if config.RAG_ENABLED and rag_stack is not None and llm_client is not None:
        try:
            settings = RagRetrievalSettings(
                planner_max_queries=4,
                top_k_per_query=6,
                max_chunks_merged=12,
                metadata_max_chunks=10,
                golden_top_k=2,
                golden_query_count=2,
                planner_max_tokens=600,
                history_tail_messages=6,
            )
            expert, golden, _, _ = await retrieve_for_user_message(
                rag_stack,
                question,
                llm_client=llm_client,
                llm_model="deepseek-v4-flash",
                history_tail=thread_blob[-3000:] or None,
                settings=settings,
                user_id=user_id,
                user_storage=user_storage,
            )
            rag_used = True
            if not expert:
                expert = about
        except Exception as e:
            logger.warning("batch answer RAG failed: %s", e)
            expert = about

    user_block = (
        f"Вопрос участника:\n{question}\n\n"
        f"Лента (фрагмент):\n{thread_blob[-5000:]}\n\n"
        f"CONTEXT:\n{(expert or '')[:6000]}\n\n"
        f"GOLDEN:\n{(golden or '')[:2000]}"
    )
    answer, _ = await logged_deepseek_chat(
        user_storage,
        user_id=user_id,
        request_kind=CLUB_TOPIC_ASSIST_ANSWER,
        api_key=api_key,
        system=TOPIC_ASSIST_ANSWER_SYSTEM,
        user=user_block,
        temperature=0.35,
        max_tokens=500,
        timeout_sec=60.0,
        metadata={"feature": "club_topic_assist", "phase": "answer"},
    )
    text = (answer or "").strip()
    if not text:
        text = (
            "Не нашёл точного ответа в материалах клуба. "
            "Напишите, пожалуйста, в поддержку — там подскажут точнее."
        )
    return text, rag_used


async def run_topic_assist_batch(
    user_storage,
    *,
    api_key: str,
    llm_client: Optional["AsyncOpenAI"] = None,
    rag_stack: Optional["RagStack"] = None,
) -> List[BatchAnswer]:
    pool = getattr(user_storage, "pool", None)
    chat_id = int(config.CLUB_GROUP_ID or 0)
    thread_id = int(config.CLUB_TOPIC_ASSIST_THREAD_ID or 0)
    if not pool or not chat_id or not thread_id:
        return []

    ctx_start, cand_start, cand_end = batch_windows()
    ctx_rows = await fetch_topic_messages(
        pool,
        chat_id=chat_id,
        thread_id=thread_id,
        since=ctx_start,
        until=cand_start,
    )
    cand_rows = await fetch_topic_messages(
        pool,
        chat_id=chat_id,
        thread_id=thread_id,
        since=cand_start,
        until=cand_end,
    )
    # только user-сообщения как кандидаты
    cand_rows = [
        r
        for r in cand_rows
        if str(r.get("sender_type") or "user") == "user"
        and int(r.get("telegram_message_id") or 0) > 0
    ]
    if not cand_rows:
        logger.info(
            "topic_assist batch: no candidates %s..%s",
            cand_start.isoformat(),
            cand_end.isoformat(),
        )
        return []

    cand_ids = {int(r["telegram_message_id"]) for r in cand_rows}
    answered = await fetch_answered_source_ids(
        pool, chat_id=chat_id, source_ids=list(cand_ids)
    )
    open_rows = [
        r for r in cand_rows if int(r["telegram_message_id"]) not in answered
    ]
    if not open_rows:
        logger.info("topic_assist batch: all candidates already answered")
        return []

    if config.CLUB_TOPIC_ASSIST_PILOT_ONLY:
        filtered = []
        for r in open_rows:
            uid = int(r["user_id"])
            if await user_in_pilot_cohort(user_storage, uid):
                filtered.append(r)
        open_rows = filtered
        if not open_rows:
            logger.info("topic_assist batch: no pilot candidates")
            return []

    open_ids = {int(r["telegram_message_id"]) for r in open_rows}
    bot_replies = await fetch_recent_bot_replies(
        pool,
        chat_id=chat_id,
        thread_id=thread_id,
        since=ctx_start,
    )

    context_blob = format_transcript(ctx_rows)
    candidates_blob = format_transcript(open_rows, candidate_ids=open_ids)
    bot_blob = format_bot_replies_blob(bot_replies)
    thread_blob = "\n".join(
        x for x in (context_blob, bot_blob, candidates_blob) if x
    )

    items = await triage_batch(
        user_storage,
        api_key=api_key,
        candidates_blob=candidates_blob,
        context_blob=context_blob,
        bot_replies_blob=bot_blob,
        already_answered=sorted(answered),
    )

    by_mid = {int(r["telegram_message_id"]): r for r in open_rows}
    max_n = int(config.CLUB_TOPIC_ASSIST_MAX_ANSWERS_PER_BATCH or 5)
    results: List[BatchAnswer] = []
    seen_mid: set = set()

    for item in items:
        if len(results) >= max_n:
            break
        mid = item.reply_to_message_id
        if mid in seen_mid or mid not in by_mid:
            continue
        if mid in answered:
            continue
        row = by_mid[mid]
        if int(row["user_id"]) != int(item.user_id):
            # доверяем msg_id, поправляем user_id
            item = TriageItem(
                reply_to_message_id=mid,
                user_id=int(row["user_id"]),
                visibility=item.visibility,
                question_summary=item.question_summary,
                reason=item.reason,
            )
        question = (row.get("content") or "").strip()
        if item.question_summary:
            question = f"{question}\n(суть: {item.question_summary})"
        answer, rag_used = await compose_one_answer(
            user_storage,
            user_id=item.user_id,
            question=question,
            thread_blob=thread_blob,
            api_key=api_key,
            llm_client=llm_client,
            rag_stack=rag_stack,
        )
        results.append(BatchAnswer(item=item, answer=answer, rag_used=rag_used))
        seen_mid.add(mid)

    logger.info(
        "topic_assist batch: candidates=%s open=%s triage=%s answers=%s window=%s..%s",
        len(cand_rows),
        len(open_rows),
        len(items),
        len(results),
        cand_start.isoformat(),
        cand_end.isoformat(),
    )
    return results
