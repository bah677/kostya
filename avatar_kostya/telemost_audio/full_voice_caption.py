"""LLM: короткая подпись к полной записи эфира / молитвы."""

from __future__ import annotations

import json
import logging
import re
from html import escape as html_escape

from bot.utils.telegram_html import sanitize_telegram_html
from telemost_audio.recording_kind import KIND_EFIR, KIND_MOLITVA

logger = logging.getLogger(__name__)

_SYSTEM = """Ты копирайтер духовного клуба «ЛЮБЯЩИЕ БОГА».

Напиши подпись к полной аудио-записи для Telegram (голосовое сообщение).

Требования:
- 2–3 коротких предложения, тёплый живой стиль;
- чтобы захотелось нажать и послушать всю запись;
- без кликбейта, честно и по делу;
- без markdown и HTML — только plain text.

Верни ТОЛЬКО JSON:
{
  "caption": "..."
}"""


def _fallback_caption(
    *,
    meeting_title: str,
    summary: str,
    recording_kind: str,
) -> str:
    title = (meeting_title or "Запись").strip()
    base = (summary or title).strip()
    if recording_kind == KIND_MOLITVA:
        return (
            f"Молитва из клуба «Любящие Бога»: {base[:180]}. "
            "Присоединяйтесь — здесь мы учимся говорить с Богом от сердца."
        )
    if recording_kind == KIND_EFIR:
        return (
            f"Эфир «{title[:100]}»: {base[:160]}. "
            "Послушайте — в записи много живого, что помогает услышать Бога."
        )
    return base[:400]


async def build_full_voice_caption(
    *,
    meeting_title: str,
    summary: str,
    transcript_excerpt: str,
    recording_kind: str,
    philosophy_hint: str = "",
) -> str:
    from config import config

    kind = (recording_kind or "").strip().lower()
    kind_label = "молитва" if kind == KIND_MOLITVA else "эфир"
    title = (meeting_title or "Запись").strip()
    excerpt = (transcript_excerpt or summary or title)[:2500]
    hint = (philosophy_hint or "").strip()

    user = (
        f"Тип записи: {kind_label}\n"
        f"Название: {title}\n"
        f"Краткое описание: {(summary or '')[:600]}\n\n"
        f"Фрагмент расшифровки:\n{excerpt[:1800]}"
    )
    if hint:
        user = f"Философия клуба:\n{hint}\n\n{user}"

    caption_plain = ""
    key = (config.OPENAI_API_KEY or "").strip()
    model = (getattr(config, "RAG_TAG_MODEL", None) or "gpt-4o-mini").strip()
    if key and excerpt.strip():
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=key)
            r = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": user[:12000]},
                ],
                max_tokens=350,
                temperature=0.7,
                response_format={"type": "json_object"},
            )
            raw = r.choices[0].message.content if r.choices else ""
            m = re.search(r"\{[\s\S]*\}", raw or "")
            if m:
                data = json.loads(m.group(0))
                caption_plain = str(data.get("caption") or "").strip()
        except Exception as e:
            logger.warning("build_full_voice_caption LLM: %s", e)

    if not caption_plain:
        caption_plain = _fallback_caption(
            meeting_title=title,
            summary=summary,
            recording_kind=kind,
        )

    text = sanitize_telegram_html(html_escape(caption_plain.strip()))
    if len(text) > 1024:
        text = text[:1021].rstrip() + "…"
    return text
