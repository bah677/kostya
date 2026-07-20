"""LLM: название + описание полной записи (эфир / молитва / покаяние / вопрос-ответ)."""

from __future__ import annotations

import json
import logging
import re
from html import escape as html_escape

from bot.utils.telegram_html import sanitize_telegram_html
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

Тебе дают расшифровку (содержание) записи. На её основе ты ОБЯЗАН придумать:

1) Красивое НАЗВАНИЕ записи
2) Краткое ОПИСАНИЕ

Название:
- в начале ОБЯЗАТЕЛЬНО укажи тип записи ровно так (с точки / двоеточия):
  «Эфир. …», «Молитва. …», «Покаяние. …», «Эфир Вопрос и Ответы. …»
  (какой тип — смотри поле «Тип записи» в запросе);
- дальше — глубокое, цепляющее название с ощущением драматургии того, что происходило;
- побуждает к размышлению и к прочтению описания;
- отражает суть и атмосферу именно этой записи (не шаблон «Запись встречи от …»);
- без кликбейта и сенсаций.

Описание — тёплое, честное, в хорошем смысле «продающее» (чтобы захотелось послушать), живым языком. Без markdown и HTML в полях title/description — только plain text.

Правила по ТИПУ записи (смотри поле «Тип записи» в запросе):

• Молитва — о чём была молитва; за что и как молились; о чём говорили с Богом; кому и в какой ситуации эта молитва особенно подойдёт.

• Эфир (тематический) — раскрой тему и суть: что разбиралось; ключевые линии/акценты (можно коротко перечислить); кому подойдёт этот эфир.

• Вопрос-ответ — какие темы и вопросы поднимались; что обсуждалось; чем и как запись может быть полезна слушателю.

• Покаяние — какая это история; что происходило; атмосфера и смысл; для кого это пространство откликнется.

Верни ТОЛЬКО JSON:
{
  "title": "...",
  "description": "..."
}"""


def _kind_brief(recording_kind: str) -> str:
    if recording_kind == KIND_MOLITVA:
        return (
            "МОЛИТВА: в описании — о чём молились, за что и как; "
            "кому и в какой ситуации подойдёт эта молитва."
        )
    if recording_kind == KIND_POKAYANIE:
        return (
            "ПОКАЯНИЕ: в описании — какая история, что происходило, "
            "смысл и атмосфера; для кого это отзовётся."
        )
    if recording_kind == KIND_QA:
        return (
            "ВОПРОС-ОТВЕТ (в названии префикс «Эфир Вопрос и Ответы»): "
            "какие темы/вопросы поднимались, что обсуждалось; чем запись полезна."
        )
    if recording_kind == KIND_EFIR:
        return (
            "ТЕМАТИЧЕСКИЙ ЭФИР: в описании — суть темы, что разбиралось "
            "(можно кратко перечислить акценты); кому подойдёт эфир."
        )
    return "Составь цепляющее название и содержательное описание по расшифровке."


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
    kind_label = KIND_LABELS.get(kind, "запись")
    # Старое название из почты/классификатора — только ориентир, не копировать.
    old_title = (meeting_title or "").strip()
    excerpt = (transcript_excerpt or summary or old_title or "").strip()
    hint = (philosophy_hint or "").strip()

    user_parts = [
        f"Тип записи: {kind_label}",
        _kind_brief(kind),
        "",
        "Ниже — содержание записи (расшифровка). "
        "Придумай НОВОЕ красивое название и описание по правилам. "
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
                max_tokens=550,
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
                # На случай старого формата: всё в одном поле caption
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

    # Название жирным в Telegram HTML
    lines = caption_plain.split("\n", 1)
    if len(lines) == 2 and lines[0].strip() and lines[1].strip():
        head = sanitize_telegram_html(html_escape(lines[0].strip()))
        body = sanitize_telegram_html(html_escape(lines[1].strip()))
        text = f"<b>{head}</b>\n\n{body}"
    else:
        text = sanitize_telegram_html(html_escape(caption_plain.strip()))

    if len(text) > 1024:
        text = text[:1021].rstrip() + "…"
    return text
