"""LLM: название + описание полной записи (эфир / молитва / покаяние / вопрос-ответ)."""

from __future__ import annotations

import json
import logging
import re

from telemost_audio.recording_kind import (
    KIND_EFIR,
    KIND_LABELS,
    KIND_MOLITVA,
    KIND_POKAYANIE,
    KIND_QA,
    ensure_kind_title_prefix,
)

logger = logging.getLogger(__name__)

_SYSTEM = """Ты копирайтер духовного клуба «ЛЮБЯЩИЕ БОГА».

Тебе дают расшифровку записи. На её основе придумай:

1) НАЗВАНИЕ — должно ЦЕПЛЯТЬ, а не быть кратким содержанием подкаста.
2) ОПИСАНИЕ — разворачивает и усиливает главную мысль записи.

Название:
- в начале ОБЯЗАТЕЛЬНО укажи тип записи ровно так:
  «Эфир. …», «Молитва. …», «Покаяние. …», «Эфир Вопрос и Ответы. …»
  (какой тип — смотри поле «Тип записи» в запросе);
- это НЕ пересказ тем («о чём говорили»);
- это крючок: напряжение, обещание смысла, внутренний вопрос;
- побуждает открыть описание и послушать;
- отражает атмосферу именно этой записи;
- без кликбейта и сенсаций, без шаблона «Запись встречи от …».

Описание:
- цель — РАЗВЕРНУТЬ мысль, которая звучит в записи, и УСИЛИТЬ её;
- человек не должен додумывать контекст: фраза из эфира без контекста пустая — ты даёшь опору;
- НЕ список тем и НЕ «краткое содержание выпуска»;
- 2–4 коротких абзаца / абзаца-абзаца живым языком;
- тёплое, честное, в хорошем смысле «продающее» (чтобы захотелось послушать);
- без markdown и HTML в полях title/description — только plain text.

Правила по ТИПУ записи (смотри поле «Тип записи»):

• Молитва — о чём была молитва и какое сердцебиение она несёт; кому особенно отзовётся.
• Эфир (тематический) — главная мысль эфира развёрнута и усилена; не перечень подтем.
• Вопрос-ответ — какая правда/ясность вызрела в ответах; чем это меняет слушателя.
• Покаяние — какая история и какое пространство открывается; для кого это откликнется.

Верни ТОЛЬКО JSON:
{
  "title": "...",
  "idea_core": "главная мысль записи в 1–2 фразах",
  "description": "..."
}"""


def _kind_brief(recording_kind: str) -> str:
    if recording_kind == KIND_MOLITVA:
        return (
            "МОЛИТВА: название — крючок к сердцу молитвы; "
            "описание разворачивает, о чём и с каким сердцем молились, и усиливает эту линию."
        )
    if recording_kind == KIND_POKAYANIE:
        return (
            "ПОКАЯНИЕ: название цепляет атмосферу честности; "
            "описание разворачивает историю/смысл, а не перечисляет этапы."
        )
    if recording_kind == KIND_QA:
        return (
            "ВОПРОС-ОТВЕТ (префикс «Эфир Вопрос и Ответы»): "
            "название — крючок к главной ясности; описание усиливает мысль ответов, не список вопросов."
        )
    if recording_kind == KIND_EFIR:
        return (
            "ТЕМАТИЧЕСКИЙ ЭФИР: название цепляет, не пересказывает тему; "
            "описание разворачивает и усиливает главную мысль эфира."
        )
    return "Составь цепляющее название и описание, которое усиливает мысль записи."


def _format_caption(title: str, description: str) -> str:
    t = (title or "").strip()
    d = (description or "").strip()
    if t and d:
        return f"{t}\n\n{d}"
    return t or d


def _fallback_caption(
    *,
    meeting_title: str,
    summary: str,
    recording_kind: str,
) -> str:
    title = (meeting_title or "Запись").strip()
    base = (summary or title).strip()
    if recording_kind == KIND_MOLITVA:
        return _format_caption(
            title if title != "Запись" else "Молитва от сердца",
            f"{base[:220]}. Молитва для тех, кто хочет говорить с Богом честно и по-живому.",
        )
    if recording_kind == KIND_POKAYANIE:
        return _format_caption(
            title if title != "Запись" else "Пространство покаяния",
            f"{base[:220]}. Для тех, кому важно быть честным перед Богом и собой.",
        )
    if recording_kind == KIND_QA:
        return _format_caption(
            title if title != "Запись" else "Живые вопросы и ответы",
            f"{base[:220]}. Полезно, если ищете ясность в темах, которые звучали в разговоре.",
        )
    if recording_kind == KIND_EFIR:
        return _format_caption(
            title if title != "Запись" else "Эфир, который стоит услышать",
            f"{base[:220]}. Для тех, кому близка эта тема и хочется пройти её глубже.",
        )
    return _format_caption(title, base[:400])


async def build_full_voice_caption_parts(
    *,
    meeting_title: str,
    summary: str,
    transcript_excerpt: str,
    recording_kind: str,
    philosophy_hint: str = "",
) -> tuple[str, str, str]:
    """Возвращает (title_plain, description_plain, caption_html)."""
    from config import config

    kind = (recording_kind or "").strip().lower()
    kind_label = KIND_LABELS.get(kind, "запись")
    old_title = (meeting_title or "").strip()
    excerpt = (transcript_excerpt or summary or old_title or "").strip()
    hint = (philosophy_hint or "").strip()

    user_parts = [
        f"Тип записи: {kind_label}",
        _kind_brief(kind),
        "",
        "Ниже — содержание записи (расшифровка).",
        "Придумай НОВОЕ цепляющее название (не краткое содержание) "
        "и описание, которое РАЗВОРАЧИВАЕТ и УСИЛИВАЕТ главную мысль.",
        "Не копируй служебные темы писем вроде «Запись встречи от …».",
        "",
    ]
    if old_title and not old_title.lower().startswith("запись встречи"):
        user_parts.append(f"Старое/черновое название (можно игнорировать): {old_title}")
    if summary:
        user_parts.append(f"Краткое summary (ориентир): {summary[:800]}")
    user_parts.extend(["", "Содержание / расшифровка:", excerpt[:10000]])
    user = "\n".join(user_parts)
    if hint:
        user = f"Философия клуба:\n{hint}\n\n{user}"

    title_plain = ""
    desc_plain = ""
    key = (config.OPENAI_API_KEY or "").strip()
    model = (getattr(config, "RAG_TAG_MODEL", None) or "gpt-4o-mini").strip()
    if key and excerpt:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=key)
            r = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": user[:14000]},
                ],
                max_tokens=700,
                temperature=0.75,
                response_format={"type": "json_object"},
            )
            raw = r.choices[0].message.content if r.choices else ""
            m = re.search(r"\{[\s\S]*\}", raw or "")
            if m:
                data = json.loads(m.group(0))
                title_plain = str(data.get("title") or "").strip()
                desc_plain = str(
                    data.get("description") or data.get("caption") or ""
                ).strip()
                if not title_plain and desc_plain and "\n" in desc_plain:
                    first, _, rest = desc_plain.partition("\n")
                    if len(first) < 120 and rest.strip():
                        title_plain = first.strip()
                        desc_plain = rest.strip()
        except Exception as e:
            logger.warning("build_full_voice_caption LLM: %s", e)

    if title_plain or desc_plain:
        title_plain = ensure_kind_title_prefix(title_plain or old_title, kind)
        caption_plain = _format_caption(title_plain, desc_plain)
    else:
        caption_plain = _fallback_caption(
            meeting_title=ensure_kind_title_prefix(old_title or "Запись", kind),
            summary=summary,
            recording_kind=kind,
        )
        lines = caption_plain.split("\n", 1)
        title_plain = lines[0].strip() if lines else ""
        desc_plain = lines[1].strip() if len(lines) > 1 else ""

    from telemost_audio.caption_revision import format_title_description_html

    return title_plain, desc_plain, format_title_description_html(title_plain, desc_plain)


async def build_full_voice_caption(
    *,
    meeting_title: str,
    summary: str,
    transcript_excerpt: str,
    recording_kind: str,
    philosophy_hint: str = "",
) -> str:
    _title, _desc, html = await build_full_voice_caption_parts(
        meeting_title=meeting_title,
        summary=summary,
        transcript_excerpt=transcript_excerpt,
        recording_kind=recording_kind,
        philosophy_hint=philosophy_hint,
    )
    return html
