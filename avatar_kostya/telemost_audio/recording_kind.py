"""Тип записи при загрузке Телемоста в RAG."""

from __future__ import annotations

from typing import Optional

KIND_EFIR = "efir"
KIND_MOLITVA = "molitva"
KIND_POKAYANIE = "pokayanie"
KIND_QA = "qa"
KIND_OTHER = "other"

_MEDIA_KINDS = frozenset({KIND_EFIR, KIND_MOLITVA, KIND_POKAYANIE, KIND_QA})

KIND_LABELS = {
    KIND_EFIR: "Эфир",
    KIND_MOLITVA: "Молитва",
    KIND_POKAYANIE: "Покаяние",
    KIND_QA: "Эфир Вопрос и Ответы",
    KIND_OTHER: "Другое",
}

# Префикс в названии полной записи.
KIND_TITLE_PREFIX = {
    KIND_EFIR: "Эфир",
    KIND_MOLITVA: "Молитва",
    KIND_POKAYANIE: "Покаяние",
    KIND_QA: "Эфир Вопрос и Ответы",
}


def is_media_recording_kind(kind: Optional[str]) -> bool:
    return (kind or "").strip().lower() in _MEDIA_KINDS


def wants_shorts_clips(kind: Optional[str]) -> bool:
    """Молитвы: только RAG + полная запись, без нарезки шортсов."""
    k = (kind or "").strip().lower()
    return k in _MEDIA_KINDS and k != KIND_MOLITVA


def ensure_kind_title_prefix(title: str, kind: Optional[str]) -> str:
    """Гарантирует префикс типа в начале названия."""
    t = (title or "").strip()
    prefix = KIND_TITLE_PREFIX.get((kind or "").strip().lower(), "")
    if not prefix:
        return t
    if not t:
        return prefix

    low = t.casefold()
    pref = prefix.casefold()
    if low.startswith(pref):
        rest = t[len(prefix) :].lstrip(" .:—–-")
        return f"{prefix}. {rest}" if rest else prefix

    # Варианты для QA без нужного префикса
    if pref.startswith("эфир вопрос"):
        for alt in (
            "вопрос-ответ",
            "вопрос и ответ",
            "вопросы и ответы",
            "q&a",
            "qa",
        ):
            if low.startswith(alt):
                rest = t[len(alt) :].lstrip(" .:—–-")
                return f"{prefix}. {rest}" if rest else prefix

    return f"{prefix}. {t}"


def recording_kind_from_content_type(content_type: Optional[str]) -> str:
    """Восстановить kind по content_type из RAG."""
    t = (content_type or "").strip().lower()
    if "покаян" in t:
        return KIND_POKAYANIE
    if "молитв" in t:
        return KIND_MOLITVA
    if "вопрос" in t or "q&a" in t or t == "qa":
        return KIND_QA
    if "эфир" in t:
        return KIND_EFIR
    return KIND_EFIR


def apply_recording_kind_to_classification(
    classification,
    recording_kind: Optional[str],
):
    """Подменяет content_type / category / product по выбранному типу записи."""
    from telemost_mail.classifier_llm import TelemostClassification

    kind = (recording_kind or "").strip().lower()
    if kind == KIND_EFIR:
        return TelemostClassification(
            is_club_meeting=True,
            recommend_index=classification.recommend_index,
            title=classification.title,
            meeting_topic=classification.meeting_topic,
            content_type="Эфир",
            content_category="webinar",
            product=classification.product or "Клуб",
            tags=classification.tags,
            summary=classification.summary,
            admin_note=classification.admin_note,
            reason=classification.reason,
        )
    if kind == KIND_MOLITVA:
        return TelemostClassification(
            is_club_meeting=True,
            recommend_index=classification.recommend_index,
            title=classification.title,
            meeting_topic=classification.meeting_topic,
            content_type="Молитва",
            content_category="educational",
            product=classification.product or "Клуб",
            tags=classification.tags,
            summary=classification.summary,
            admin_note=classification.admin_note,
            reason=classification.reason,
        )
    if kind == KIND_POKAYANIE:
        return TelemostClassification(
            is_club_meeting=True,
            recommend_index=classification.recommend_index,
            title=classification.title,
            meeting_topic=classification.meeting_topic,
            content_type="Покаяние",
            content_category="educational",
            product=classification.product or "Клуб",
            tags=classification.tags,
            summary=classification.summary,
            admin_note=classification.admin_note,
            reason=classification.reason,
        )
    if kind == KIND_QA:
        return TelemostClassification(
            is_club_meeting=True,
            recommend_index=classification.recommend_index,
            title=classification.title,
            meeting_topic=classification.meeting_topic,
            content_type="Вопрос-ответ",
            content_category="educational",
            product=classification.product or "Клуб",
            tags=classification.tags,
            summary=classification.summary,
            admin_note=classification.admin_note,
            reason=classification.reason,
        )
    return classification
