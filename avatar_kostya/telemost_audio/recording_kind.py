"""Тип записи при загрузке Телемоста в RAG."""

from __future__ import annotations

from typing import Optional

KIND_EFIR = "efir"
KIND_MOLITVA = "molitva"
KIND_OTHER = "other"

_MEDIA_KINDS = frozenset({KIND_EFIR, KIND_MOLITVA})


def is_media_recording_kind(kind: Optional[str]) -> bool:
    return (kind or "").strip().lower() in _MEDIA_KINDS


def apply_recording_kind_to_classification(
    classification,
    recording_kind: Optional[str],
):
    """Подменяет content_type / category / product для Эфира и Молитвы."""
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
    return classification
