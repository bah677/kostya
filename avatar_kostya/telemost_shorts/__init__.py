"""АРХИВ: нарезка виральных шортов из записей Телемоста.

Видео-шортсы не режем: модуль выключен флагом
TELEMOST_VIDEO_SHORTS_ENABLED=0 и не исполняется.
Известные дефекты и как их чинить — telemost_shorts/README.md.
"""

from telemost_shorts.pipeline import enqueue_telemost_shorts, enqueue_telemost_shorts_last

__all__ = ["enqueue_telemost_shorts", "enqueue_telemost_shorts_last"]
