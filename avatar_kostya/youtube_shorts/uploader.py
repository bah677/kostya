"""Загрузка Shorts на YouTube — слоты каждые 3 часа МСК."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from youtube_prayer.metadata import VideoMetadata
from youtube_prayer.langs import normalize_lang, premiere_hours_for
from youtube_prayer.premiere_schedule import parse_premiere_hours
from youtube_prayer.youtube_uploader import (
    YoutubeUploadResult,
    upload_premiere_if_enabled,
)

logger = logging.getLogger(__name__)

_DEFAULT_SHORTS_HOURS = "0,3,6,9,12,15,18,21"


def _cfg(name: str, default=None):
    try:
        from config import config as cfg

        return getattr(cfg, name, default)
    except Exception:
        return default


def shorts_upload_enabled() -> bool:
    explicit = _cfg("YT_SHORTS_YOUTUBE_UPLOAD_ENABLED", None)
    if explicit is not None:
        return bool(explicit)
    return bool(_cfg("YT_PRAYER_YOUTUBE_UPLOAD_ENABLED", False))


def shorts_premiere_hours_msk(lang: str = "ru") -> List[int]:
    """Часы премьер шортсов — в поясе аудитории языка.

    Имя с суффиксом _MSK осталось историческим: для русского канала часы
    действительно московские, для испанского это часы Мехико.
    """
    hours = premiere_hours_for(
        lang, env_prefix="YT_SHORTS_YOUTUBE_PREMIERE_HOURS_MSK"
    )
    return hours or parse_premiere_hours(_DEFAULT_SHORTS_HOURS)


async def upload_short_premiere_if_enabled(
    *,
    video_path: Path,
    thumbnail_path: Optional[Path],
    metadata: VideoMetadata,
    day: str,
    index: int,
    work_dir: Path,
    lang: str = "ru",
) -> Optional[YoutubeUploadResult]:
    """Идемпотентная загрузка Short с премьерой в слот index (1–8)."""
    if not shorts_upload_enabled():
        return None
    # Раньше здесь стояло lang="ru" наглухо: испанский ролик уехал бы на
    # русский канал, в русских метаданных и в московское время.
    lang = normalize_lang(lang)
    return await upload_premiere_if_enabled(
        video_path=video_path,
        thumbnail_path=thumbnail_path,
        metadata=metadata,
        lang=lang,
        day=day,
        index=index,
        work_dir=work_dir,
        slots_msk=shorts_premiere_hours_msk(lang),
    )
