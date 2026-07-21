"""Итеративная доработка title/description по замечаниям админа."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_REVISION_SYSTEM = """Ты редактор подписей духовного клуба «ЛЮБЯЩИЕ БОГА».

Это НЕ новая генерация с чистого листа.
Тебе дают:
- исходный контекст записи/отрывка;
- текущую версию title/description (или headline/summary);
- историю предыдущих замечаний админа;
- новое замечание.

Задача: улучшить ТЕКУЩУЮ версию с учётом замечания, сохранив сильные стороны.
Не откатывай удачные формулировки без причины.
Название должно цеплять (не быть кратким содержанием).
Описание должно разворачивать и усиливать мысль, чтобы не приходилось додумывать контекст.

Верни ТОЛЬКО JSON:
{
  "title": "...",
  "description": "...",
  "note": "кратко что изменил (1 предложение)"
}
"""


@dataclass(frozen=True)
class CaptionRevisionResult:
    title: str
    description: str
    note: str = ""


def format_title_description_html(title: str, description: str) -> str:
    from html import escape as html_escape

    from bot.utils.telegram_html import sanitize_telegram_html

    t = (title or "").strip()
    d = (description or "").strip()
    if t and d:
        text = f"<b>{sanitize_telegram_html(html_escape(t))}</b>\n\n{sanitize_telegram_html(html_escape(d))}"
    else:
        text = sanitize_telegram_html(html_escape(t or d))
    if len(text) > 1024:
        text = text[:1021].rstrip() + "…"
    return text


def format_audio_caption_html(
    *,
    headline: str,
    summary: str,
    bible_quote: str = "",
    bible_ref: str = "",
) -> str:
    from telemost_audio.caption_llm import _format_html_caption

    return _format_html_caption(headline, summary, bible_quote, bible_ref)


async def revise_caption_with_feedback(
    *,
    entity_type: str,
    current_title: str,
    current_description: str,
    feedback: str,
    context: Optional[Dict[str, Any]] = None,
    iterations: Optional[List[Dict[str, Any]]] = None,
) -> Optional[CaptionRevisionResult]:
    from config import config

    key = (config.OPENAI_API_KEY or "").strip()
    if not key:
        return None
    model = (getattr(config, "RAG_TAG_MODEL", None) or "gpt-4o-mini").strip()
    ctx = context or {}
    iters = iterations or []

    hist_lines: List[str] = []
    for it in iters[-12:]:
        role = (it.get("role") or "?").strip()
        content = (it.get("content") or "").strip()
        if content:
            hist_lines.append(f"{role}: {content[:1500]}")

    user_parts = [
        f"Тип сущности: {entity_type}",
        f"Тип записи: {ctx.get('recording_kind') or ctx.get('kind_label') or ''}",
        f"Встреча/эфир: {ctx.get('meeting_title') or ''}",
        "",
        "Контекст записи/отрывка:",
        str(ctx.get("transcript_excerpt") or ctx.get("clip_transcript") or "")[:6000],
        "",
        f"Текущий title/headline:\n{(current_title or '').strip()}",
        f"Текущий description/summary:\n{(current_description or '').strip()}",
    ]
    if ctx.get("bible_quote") or ctx.get("bible_ref"):
        user_parts.append(
            f"Библейская цитата (можно сохранить/уточнить, если уместно): "
            f"{ctx.get('bible_quote') or ''} {ctx.get('bible_ref') or ''}"
        )
    if hist_lines:
        user_parts.extend(["", "История правок:", *hist_lines])
    user_parts.extend(
        [
            "",
            "Новое замечание админа:",
            (feedback or "").strip()[:4000],
            "",
            "Верни улучшенные title и description.",
        ]
    )
    user = "\n".join(user_parts)

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=key)
        r = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _REVISION_SYSTEM},
                {"role": "user", "content": user[:14000]},
            ],
            max_tokens=700,
            temperature=0.55,
            response_format={"type": "json_object"},
        )
        raw = r.choices[0].message.content if r.choices else ""
        m = re.search(r"\{[\s\S]*\}", raw or "")
        if not m:
            return None
        data = json.loads(m.group(0))
        title = str(data.get("title") or current_title or "").strip()
        desc = str(data.get("description") or current_description or "").strip()
        note = str(data.get("note") or "").strip()
        if not title and not desc:
            return None
        return CaptionRevisionResult(title=title, description=desc, note=note)
    except Exception as e:
        logger.warning("revise_caption_with_feedback: %s", e)
        return None
