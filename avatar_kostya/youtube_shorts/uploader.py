"""Загрузка Shorts на YouTube — слоты каждые 3 часа МСК."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from youtube_prayer.metadata import VideoMetadata
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


def shorts_premiere_hours_msk() -> List[int]:
    raw = _cfg("YT_SHORTS_YOUTUBE_PREMIERE_HOURS_MSK", _DEFAULT_SHORTS_HOURS)
    return parse_premiere_hours(raw)


async def upload_short_premiere_if_enabled(
    *,
    video_path: Path,
    thumbnail_path: Optional[Path],
    metadata: VideoMetadata,
    day: str,
    index: int,
    work_dir: Path,
) -> Optional[YoutubeUploadResult]:
    """Идемпотентная загрузка Short с премьерой в слот index (1–8)."""
    if not shorts_upload_enabled():
        return None
    return await upload_premiere_if_enabled(
        video_path=video_path,
        thumbnail_path=thumbnail_path,
        metadata=metadata,
        lang="ru",
        day=day,
        index=index,
        work_dir=work_dir,
        slots_msk=shorts_premiere_hours_msk(),
    )
