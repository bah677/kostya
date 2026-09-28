"""LLM: название + тизер полной записи (эфир / молитва / покаяние / вопрос-ответ)."""

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
)

logger = logging.getLogger(__name__)

# Фразы-паразиты в начале тизера (черновиковый тон) — для всех типов записей.
_PARASITE_RE = re.compile(
    r"^\s*("
    r"в этой молитве\b|"
    r"в этом эфире\b|"
    r"в данном эфире\b|"
    r"в этой записи\b|"
    r"в данном (эфире|выпуске|разговоре)\b|"
    r"в этой (встрече|беседе)\b|"
    r"константин (нам |здесь )?(предлагает|говорит|разбирает)\b|"
    r"костя (нам |здесь )?(предлагает|говорит|разбирает)\b|"
    r"сегодня (мы|константин|костя)\b|"
    r"эта (молитва|запись|встреча) (представляет собой|является|о том)\b"
    r").*?[.!?]\s*",
    re.IGNORECASE | re.DOTALL,
)

_MECHANICAL_PREFIX_RE = re.compile(
    r"(?i)^(эфир вопрос и ответы|эфир|молитва|покаяние)\s*[.:—–-]\s*"
)

_SYSTEM = """Ты копирайтер духовного клуба «ЛЮБЯЩИЕ БОГА». Пишешь подписи к полным записям
(эфир, молитва, покаяние, вопрос–ответ — тип указан в запросе).

Единый почерк для ВСЕХ типов: тёплый, вдохновляющий, художественный.
Без канцелярита и без «черновикового» тона. Один стиль от записи к записи.

Что нужно на выходе:

1) НАЗВАНИЕ (поле title) — короткое, ёмкое, художественное, отражает глубинный смысл записи.
   Просто заголовок, без лишних слов.
   Примеры стиля: «Молитва о тишине в сердце», «Обретение опоры», «Свет в темноте»,
   «Когда вера молчит», «Путь через сомнение».
   Можно начать с «Молитва о…» / «Эфир о…» или дать чисто образное название.
   НЕ делай служебный шаблон «Эфир. Тема», «Молитва. Тема», «Покаяние. Тема».
   НЕ пересказывай содержание длинной фразой.

2) ТИЗЕР (поле description) — ровно 2–4 предложения. Не рассказывай, о чём запись.
   Назови состояние, в котором человек себя узнает, и намекни на поворот, который
   его там ждёт. Ответ остаётся внутри записи.
   Порядок такой:
   – первое предложение: узнавание. Конкретное состояние, а не «трудности»;
   – дальше: почему привычный выход не работает;
   – последнее: что откроется, если дослушать. Без спойлера.
   Тёплый язык, обращение к слушателю на «ты». Никакого пересказа по пунктам.

Учитывай тип записи из запроса (молитва / эфир / покаяние / вопрос–ответ),
но формат выхода всегда один: художественное название + тизер.

СТРОГО ЗАПРЕЩЕНО в названии и тизере:
- фразы-паразиты: «В этой молитве Константин нам предлагает…», «В этом эфире…»,
  «В данной записи…», «Сегодня мы…», «Костя говорит…»;
- мета-комментарии про автора, формат записи, «приглашает», «предлагает», «разбирает»;
- markdown, HTML, эмодзи, хэштеги;
- список тем и «краткое содержание».

Верни ТОЛЬКО JSON:
{
  "title": "...",
  "idea_core": "глубинный смысл записи в 1 фразе",
  "description": "тизер 2–4 предложения"
}"""


def _kind_brief(recording_kind: str) -> str:
    if recording_kind == KIND_MOLITVA:
        return (
            "Тип: МОЛИТВА. Название — художественное; "
            "тизер — состояния в молитве, о чём она, что даёт после."
        )
    if recording_kind == KIND_POKAYANIE:
        return (
            "Тип: ПОКАЯНИЕ. Название — художественное, атмосфера честности; "
            "тизер — состояния, суть пространства, что остаётся после."
        )
    if recording_kind == KIND_QA:
        return (
            "Тип: ВОПРОС–ОТВЕТ. Название — художественное к главной ясности; "
            "тизер — состояния слушателя, о чём ясность, что меняется после."
        )
    if recording_kind == KIND_EFIR:
        return (
            "Тип: ЭФИР. Название — художественное к главной мысли; "
            "тизер — состояния, о чём эфир, что даёт после."
        )
    return (
        "Художественное название + тизер 2–4 предложения "
        "(состояния, о чём, что даёт после)."
    )


def _format_caption(title: str, description: str) -> str:
    t = (title or "").strip()
    d = (description or "").strip()
    if t and d:
        return f"{t}\n\n{d}"
    return t or d


def _strip_parasites(text: str) -> str:
    """Убирает типичные черновиковые зачины в тизере."""
    out = (text or "").strip()
    for _ in range(3):
        cleaned = _PARASITE_RE.sub("", out, count=1).strip()
        if cleaned == out:
            break
        out = cleaned
    return out


def _normalize_title(title: str, *, recording_kind: str = "") -> str:
    """Художественное название без механического «Тип. …»."""
    t = re.sub(r"\s+", " ", (title or "").strip()).strip(" «»\"'")
    t = _MECHANICAL_PREFIX_RE.sub("", t).strip()
    if not t:
        defaults = {
            KIND_MOLITVA: "Молитва от сердца",
            KIND_POKAYANIE: "Пространство честности",
            KIND_QA: "Ясность в вопросах",
            KIND_EFIR: "Свет в пути",
        }
        return defaults.get((recording_kind or "").strip().lower(), "Запись от сердца")
    low = t.casefold()
    # После среза «Молитва.» осталось «о тишине…» → вернуть «Молитва о…»
    if (recording_kind or "").strip().lower() == KIND_MOLITVA and low.startswith(
        ("о ", "об ", "обо ", "за ", "для ")
    ):
        return f"Молитва {t}"
    return t


def _fallback_caption(
    *,
    meeting_title: str,
    summary: str,
    recording_kind: str,
) -> str:
    title = _normalize_title(
        meeting_title if (meeting_title or "").strip() not in ("", "Запись") else "",
        recording_kind=recording_kind,
    )
    base = (summary or title).strip()
    teaser = (
        f"{base[:180].rstrip('.')}. "
        "Здесь можно встретить то, что тревожит сердце, "
        "и выйти с опорой и ясностью."
    )
    return _format_caption(title, teaser)


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
        "Ниже — расшифровка записи.",
        "Составь художественное НАЗВАНИЕ и ТИЗЕР (2–4 предложения) строго по правилам system.",
        "Не копируй служебные темы писем вроде «Запись встречи от …».",
        "",
    ]
    if old_title and not old_title.lower().startswith("запись встречи"):
        user_parts.append(f"Старое/черновое название (можно игнорировать): {old_title}")
    # summary сюда намеренно НЕ кладём. Его пишет классификатор писем —
    # он видит не эфир, а конспект Телемоста, и делает сухую аннотацию для
    # карточки в базе знаний. Как «ориентир» она тянула тизер обратно в
    # пересказ. Расшифровки копирайтеру теперь достаточно.
    # Вторая обрезка тут тоже была лишней: выборка приходит уже собранной
    # из начала, середины и конца, а срез от начала выбрасывал финал.
    user_parts.extend(["", "Содержание / расшифровка:", excerpt])
    user = "\n".join(user_parts)
    if hint:
        user = f"Философия клуба:\n{hint}\n\n{user}"

    title_plain = ""
    desc_plain = ""
    key = (config.OPENAI_API_KEY or "").strip()
    model = (getattr(config, "TELEMOST_AUDIO_CAPTION_MODEL", None) or "gpt-4.1").strip()
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
                temperature=0.7,
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
        title_plain = _normalize_title(title_plain or old_title, recording_kind=kind)
        desc_plain = _strip_parasites(desc_plain)
        caption_plain = _format_caption(title_plain, desc_plain)
    else:
        caption_plain = _fallback_caption(
            meeting_title=old_title or "Запись",
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
