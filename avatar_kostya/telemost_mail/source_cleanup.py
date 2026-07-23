"""Удаление исходных записей Телемоста после успешной нарезки/отправки."""

from __future__ import annotations

import logging
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]

# Сколько пайплайнов ещё держат исходник (audio clips + full_voice).
_source_refs: dict[str, int] = defaultdict(int)


def retain_source(path: Optional[PathLike]) -> None:
    """Зарегистрировать, что пайплайн использует исходник."""
    if not path:
        return
    key = str(Path(path).resolve()) if Path(path).exists() else str(path)
    _source_refs[key] += 1
    logger.debug("telemost cleanup: retain %s refs=%s", key, _source_refs[key])


def release_source(
    path: Optional[PathLike],
    *,
    label: str = "source",
    delete: bool = True,
) -> bool:
    """Отпустить исходник; удалить файл, когда refs == 0 и delete=True."""
    if not path:
        return False
    p = Path(path)
    key = str(p.resolve()) if p.exists() else str(p)
    cur = _source_refs.get(key, 0)
    if cur <= 1:
        _source_refs.pop(key, None)
        if delete:
            return unlink_source_media(p, label=label)
        return False
    _source_refs[key] = cur - 1
    logger.debug("telemost cleanup: release %s refs=%s", key, _source_refs[key])
    return False


def unlink_source_media(
    path: Optional[PathLike],
    *,
    label: str = "source",
) -> bool:
    """Удаляет локальный исходник (webm/mp4/ogg/m4a) после нарезки."""
    if not path:
        return False
    p = Path(path)
    try:
        if not p.is_file():
            return False
        size = p.stat().st_size
        p.unlink(missing_ok=True)
        logger.info(
            "telemost cleanup: removed %s %s (%.1fM)",
            label,
            p,
            size / (1024 * 1024),
        )
        return True
    except OSError as e:
        logger.warning("telemost cleanup: failed to remove %s %s: %s", label, p, e)
        return False


def rmtree_workdir(path: Optional[PathLike], *, label: str = "workdir") -> None:
    """Удаляет временную папку нарезки (клипы уже отправлены в Telegram)."""
    if not path:
        return
    p = Path(path)
    if not p.exists():
        return
    try:
        shutil.rmtree(p, ignore_errors=True)
        logger.info("telemost cleanup: removed %s %s", label, p)
    except OSError as e:
        logger.warning("telemost cleanup: workdir %s: %s", p, e)
