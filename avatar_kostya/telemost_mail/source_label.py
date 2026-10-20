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
# «Конспект встречи от 12.08.2026», «Запись встречи от …» — это тема письма,
# а не название: дата в имени уже есть, смысла такой заголовок не добавляет.
_NOISE_RE = re.compile(
    r"^\s*(конспект|запись|стенограмма)\s+(встречи|эфира|записи)\b.*$", re.I
)


def canon_type(raw: str) -> str:
    t = re.sub(r"\s+", " ", (raw or "").strip())
    return TYPE_CANON.get(t.casefold(), t[:1].upper() + t[1:] if t else "Запись")


def _clean_title(title: str, *, type_name: str) -> str:
    """Убирает из заголовка то, что уже сказано типом и датой."""
    t = re.sub(r"\s+", " ", (title or "").strip())
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


def canonical_source_label(
    *,
    content_type: str = "",
    meeting_date: str = "",
    title: str = "",
    subject: str = "",
    fallback: str = "Запись",
) -> str:
    """ТИП · ДАТА · ЗАГОЛОВОК. Части без смысла опускаются."""
    type_name = canon_type(content_type)
    date = (meeting_date or "").strip()
    if not date:
        m = _DATE_RE.search(subject or "")
        date = m.group(0) if m else ""
    clean = _clean_title(title or subject, type_name=type_name)

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
        subject=subject,
    )
