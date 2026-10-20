"""Единое имя записи для RAG и дерева контент-завода.

Дерево группирует материалы по паре (раздел, имя источника). Имя раньше
бралось из заголовка классификатора как есть — а он на 71 разной встрече
выдал одно и то же «Встреча клуба „Разговоры с Богом"». Все семьдесят одна
схлопнулись в одну карточку, и в разделе «Эфиры» вместо сотни записей было
видно два десятка.

Поэтому имя собирается из структуры: ТИП · ДАТА · ЗАГОЛОВОК. Дата делает
имя уникальным, тип — единообразным, а заголовок добавляется, только если
несёт что-то сверх первых двух.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

# Классификатор пишет тип вольно: «Эфиры» и «Эфир», «встреча клуба»
# строчными. В имени он должен выглядеть одинаково всегда.
TYPE_CANON: Dict[str, str] = {
    "эфир": "Эфир",
    "эфиры": "Эфир",
    "молитва": "Молитва",
    "молитвы": "Молитва",
    "покаяние": "Покаяние",
    "вопрос-ответ": "Вопрос-ответ",
    "вопрос ответ": "Вопрос-ответ",
    "встреча клуба": "Встреча клуба",
    "встреча": "Встреча клуба",
    "подкасты": "Подкаст",
    "подкаст": "Подкаст",
}

_DATE_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?")
# «2026-05-12_Женщина глазами Бога…» — день загрузки в индекс, который
# импортёр подставил в имя файла. Для 160 записей такой префикс совпал с
# отметкой импорта и ни разу не отличался: это не дата съёмки.
_IMPORT_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[_\s]+")
_LINK_RE = re.compile(r"\s*https?://\S+")
_YT_TAG_RE = re.compile(r"\s*\[YouTube[^\]]*\]", re.I)
# Индексатор обрезает source на 80 символах, и ссылка в конце остаётся
# огрызком: «… Часть 3(3) http», «… [YouTube http». Убираем и такой хвост.
_TAIL_LINK_RE = re.compile(r"\s*\[?(?:YouTube\b)?\s*https?\S*$", re.I)
_TAIL_TAG_RE = re.compile(r"\s*\[YouTube[^\]]*$", re.I)
# molitva_5756714519.mp3 — имя файла из телеграма. Номер оставляем: он
# ничего не рассказывает, но отличает одну запись от другой.
_AUDIO_FILE_RE = re.compile(
    r"^(molitva|efir|pokayanie|qa)_(\d+)\.(?:mp3|m4a|ogg|wav)$", re.I
)
_TOPIC_N_RE = re.compile(r"^topic_\d+$", re.I)


def _strip_noise(text: str) -> str:
    """Ссылки, служебные метки и префикс импорта — в имени записи лишние."""
    t = _YT_TAG_RE.sub("", text)
    t = _LINK_RE.sub("", t)
    t = _TAIL_TAG_RE.sub("", t)
    t = _TAIL_LINK_RE.sub("", t)
    t = _IMPORT_PREFIX_RE.sub("", t)
    return t.strip()
# «Конспект встречи от 12.08.2026», «Запись встречи от …» — это тема письма,
# а не название: дата в имени уже есть, смысла такой заголовок не добавляет.
_NOISE_RE = re.compile(
    r"^\s*(конспект|запись|стенограмма)\s+(встречи|эфира|записи)\b.*$", re.I
)


def canon_type(raw: str) -> str:
    t = re.sub(r"\s+", " ", (raw or "").strip())
    # topic_57 — внутренний номер темы форума, из которой пришли отзывы.
    if _TOPIC_N_RE.match(t):
        return "Отзыв"
    return TYPE_CANON.get(t.casefold(), t[:1].upper() + t[1:] if t else "Запись")


def _clean_title(title: str, *, type_name: str) -> str:
    """Убирает из заголовка то, что уже сказано типом и датой."""
    t = re.sub(r"\s+", " ", (title or "").strip())
    if not t or _NOISE_RE.match(t):
        return ""
    # У записей, импортированных с YouTube, имя — это имя файла целиком:
    # «2026-05-12_Сдаться Богу. Как и зачем? https://youtu.be/… [YouTube …]».
    m = _AUDIO_FILE_RE.match(t)
    if m:
        return m.group(2)
    t = _strip_noise(t)
    if not t or _NOISE_RE.match(t):
        return ""
    t = _DATE_RE.sub("", t).strip(" .,·—-")
    # «Встреча клуба «Разговоры с Богом»» при типе «Встреча клуба» —
    # остаётся только название клуба, а оно одно на все записи.
    bare = re.sub(r"[«»\"']", "", t).casefold()
    type_bare = re.sub(r"[«»\"']", "", type_name).casefold()
    if bare == type_bare or bare.startswith(type_bare):
        rest = re.sub(r"[«»\"']", "", t)[len(type_name):].strip(" .,·—-")
        # «Разговоры с Богом» повторяется в каждой записи — это не заголовок.
        if len(rest) < 4:
            return ""
        t = rest
    return t[:70].strip(" .,·—-")


def _clean_topic(topic: str) -> str:
    """О чём была встреча.

    Слово «Обсуждение» в начале не срезаем, хотя оно и повторяется часто:
    дальше идёт родительный падеж, и без него фраза ломается —
    «Молитвы, благодарности и заботы Бога» вместо «Обсуждение молитвы…».
    """
    t = _strip_noise(re.sub(r"\s+", " ", (topic or "").strip()))
    if not t:
        return ""
    t = (t[:1].upper() + t[1:])[:80]
    return t.strip(" .,:;·—-")


def _norm_date(raw: str) -> str:
    """Дата записи в виде ДД.ММ.ГГГГ. Технические отметки отбрасываются.

    В индексе два разных поля слились в одно: у записей телемоста ``date`` —
    это день встречи (ДД.ММ.ГГГГ), а у всего, что загружали пачкой, — момент
    импорта (2026-05-12T18:52:20Z). Отметки импорта сбиваются в несколько
    дней на сотню записей каждый, и показывать их как дату эфира — врать:
    104 записи «от 12 мая» на самом деле просто загружены в один день.
    """
    s = (raw or "").strip()
    if not s:
        return ""
    if _DATE_RE.match(s):
        return s[:10]
    m = _ISO_DATE_RE.match(s)
    if not m:
        return ""
    if m.group(4) is not None:
        return ""
    return f"{m.group(3)}.{m.group(2)}.{m.group(1)}"


def canonical_source_label(
    *,
    content_type: str = "",
    meeting_date: str = "",
    title: str = "",
    subject: str = "",
    topic: str = "",
    fallback: str = "Запись",
) -> str:
    """ТИП · ДАТА · О ЧЁМ. Части без смысла опускаются.

    Тема встречи (meeting_topic) берётся первой: заголовок классификатора на
    семидесяти встречах один и тот же, а тема описывает суть и почти всегда
    уникальна — 139 разных на 150 записей.
    """
    type_name = canon_type(content_type)
    date = _norm_date(meeting_date)
    if not date:
        m = _DATE_RE.search(subject or "")
        date = m.group(0) if m else ""
    clean = _clean_topic(topic) or _clean_title(title or subject, type_name=type_name)

    parts = [p for p in (type_name, date, clean) if p]
    if not parts:
        return fallback
    return " · ".join(parts)


def label_from_pending(row: Dict[str, Any]) -> str:
    """Имя по строке telemost_mail_pending (classification уже словарь)."""
    cls = row.get("classification") or {}
    if isinstance(cls, str):
        import json

        try:
            cls = json.loads(cls)
        except Exception:
            cls = {}
    extra = cls.get("extra") or {}
    return canonical_source_label(
        content_type=str(cls.get("content_type") or ""),
        meeting_date=str(extra.get("meeting_date") or ""),
        title=str(cls.get("title") or ""),
        topic=str(cls.get("meeting_topic") or ""),
        subject=str(row.get("subject") or ""),
    )


def label_from_classification(
    classification: Any, *, subject: str = "", extra: Optional[Dict[str, Any]] = None
) -> str:
    """Имя по объекту классификации из почтового пайплайна."""
    ct = getattr(classification, "content_type", "") or ""
    title = getattr(classification, "title", "") or ""
    ex = extra or {}
    return canonical_source_label(
        content_type=str(ct),
        meeting_date=str(ex.get("meeting_date") or ""),
        title=str(title),
        topic=str(getattr(classification, "meeting_topic", "") or ""),
        subject=subject,
    )
