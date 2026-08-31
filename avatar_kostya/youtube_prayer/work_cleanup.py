"""Удаление тяжёлых исходников после успешной выкладки на YouTube."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Маркеры / мелкий текст — оставляем для идемпотентности и отладки.
_KEEP_NAMES = frozenset(
    {
        "youtube_upload.json",
        "meta.json",
        "topic.json",
        "prayer.txt",
        "title.txt",
        "description.txt",
        "thumbnail_title.txt",
        "hook_question.txt",
        "cover_title.txt",
        "cover_title_vertical.txt",
        "word_timings.json",
        "done.json",
        "compose_error.json",
    }
)

_HEAVY_SUFFIXES = frozenset(
    {
        ".mp4",
        ".mov",
        ".mkv",
        ".webm",
        ".wav",
        ".flac",
        ".ass",
        ".ssa",
    }
)

_HEAVY_DIR_PREFIXES = ("broll_",)

# Крупные промежуточные PNG (фоны обложек / AI-кадры).
_HEAVY_NAME_PREFIXES = ("cover_bg_", "openai_img_", "broll_")


def _is_heavy_file(path: Path) -> bool:
    name = path.name
    if name in _KEEP_NAMES:
        return False
    if path.suffix.lower() in _HEAVY_SUFFIXES:
        return True
    if path.suffix.lower() in {".ogg", ".mp3", ".m4a"} and name.startswith("prayer_"):
        return True
    if any(name.startswith(p) for p in _HEAVY_NAME_PREFIXES):
        return True
    return False


def cleanup_item_media_after_youtube_upload(item_dir: Path) -> int:
    """
    Удаляет тяжёлые медиа/исходники в item_dir после успешного upload.
    Возвращает суммарный освобождённый размер в байтах.
    """
    if not item_dir or not item_dir.is_dir():
        return 0

    freed = 0
    removed: list[str] = []

    for child in sorted(item_dir.iterdir(), key=lambda p: p.name):
        try:
            if child.is_dir():
                if any(child.name.startswith(p) for p in _HEAVY_DIR_PREFIXES):
                    size = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
                    shutil.rmtree(child, ignore_errors=True)
                    freed += size
                    removed.append(f"{child.name}/")
                continue
            if not child.is_file():
                continue
            if not _is_heavy_file(child):
                continue
            size = child.stat().st_size
            child.unlink(missing_ok=True)
            freed += size
            removed.append(child.name)
        except OSError as e:
            logger.warning("yt cleanup: failed %s: %s", child, e)

    if removed:
        logger.info(
            "yt cleanup after upload dir=%s freed=%.1fM files=%s",
            item_dir.name,
            freed / (1024 * 1024),
            ", ".join(removed[:20]) + ("…" if len(removed) > 20 else ""),
        )
    return freed
