"""Видеоряд из банка героев вместо стока.

Берёт кадры из data/cast_bank в порядке «беда → присутствие → утешение»,
оживляет каждый медленным наездом и склеивает теми же средствами, что и
сток: Ken Burns, xfade, киногрейд, подрезка под длину озвучки. Ничего
нового в ffmpeg-части не изобретаем — она уже отлажена.

Если кадров в банке не хватает, функция честно возвращает None: лучше
собрать ролик стоком, чем показать три картинки по сорок секунд каждая.
"""

from __future__ import annotations

import logging
import os
import random
from pathlib import Path
from typing import List, Optional, Sequence

from youtube_prayer.cast import Scene, scenes_for_topic
from youtube_prayer.cast_bank import bank_dir, frames_for_scene

logger = logging.getLogger(__name__)

# Меньше четырёх кадров — это уже не видеоряд, а слайд-шоу.
_MIN_FRAMES = 4


def pick_frames(topic: str, *, count: int = 8, seed: Optional[int] = None) -> List[Path]:
    """Кадры под тему, по одному варианту на сцену, в сюжетном порядке."""
    rng = random.Random(seed)
    out: List[Path] = []
    for scene in scenes_for_topic(topic, count=count):
        variants = frames_for_scene(scene)
        if variants:
            out.append(rng.choice(variants))
    return out


def bank_ready(topic: str = "", *, count: int = 8) -> bool:
    return len(pick_frames(topic, count=count)) >= _MIN_FRAMES


async def build_cast_montage(
    work_dir: Path,
    *,
    topic: str,
    duration_sec: float,
    width: int,
    height: int,
    seed: Optional[int] = None,
) -> Optional[Path]:
    """Видеоряд из банка под длину озвучки. None — если банк не потянул."""
    import asyncio

    from youtube_prayer.stock_broll import (
        _apply_cinematic_grade,
        _env_flag,
        _env_float,
        _image_ken_burns,
        _trim_or_loop_to_duration,
        _xfade_concat,
    )

    dur = max(10.0, float(duration_sec))
    frames = pick_frames(topic, count=16, seed=seed)
    if len(frames) < _MIN_FRAMES:
        logger.warning(
            "cast: в банке %s кадров под тему %r — мало, остаёмся на стоке",
            len(frames),
            topic,
        )
        return None

    # Длину сцены считаем от озвучки: лучше показать меньше кадров подольше,
    # чем гнать слайд-шоу. Но и держать один кадр дольше 12 секунд не стоит.
    fade = _env_float("YT_PRAYER_XFADE_SEC", 0.8)
    scene_sec = max(5.0, min(12.0, dur / max(1, len(frames))))
    needed = max(_MIN_FRAMES, min(len(frames), int(dur / max(1.0, scene_sec - fade)) + 1))
    frames = frames[:needed]
    scene_sec = max(5.0, min(12.0, dur / len(frames) + fade))

    work_dir.mkdir(parents=True, exist_ok=True)
    pool = work_dir / "cast_clips"
    pool.mkdir(parents=True, exist_ok=True)

    def _render() -> Optional[Path]:
        clips: List[Path] = []
        for i, img in enumerate(frames):
            dest = pool / f"{i:02d}.mp4"
            # Направление наезда чередуем, иначе движение выглядит машинным.
            if _image_ken_burns(
                img, dest, seconds=scene_sec, width=width, height=height,
                direction=i % 4,
            ):
                clips.append(dest)
        if len(clips) < _MIN_FRAMES:
            return None
        concat = work_dir / "cast_concat.mp4"
        if not _xfade_concat(clips, concat, fade_sec=fade):
            return None
        # Киногрейд кадрам банка не нужен и вредит. Он настраивался под сток
        # с Pexels: тот приходит плоским и холодноватым, его надо вытягивать
        # и утеплять. Кадры банка уже сгенерированы в тёплой гамме, и второй
        # слой утепления даёт не «тепло», а духоту: светлый кадр с бессонницей
        # уходил в тёмно-оранжевый, лицо почти тонуло.
        source = concat
        if _env_flag("YT_PRAYER_CAST_GRADE", False):
            graded = work_dir / "cast_graded.mp4"
            if _apply_cinematic_grade(concat, graded):
                source = graded
        out = work_dir / "cast_montage.mp4"
        if not _trim_or_loop_to_duration(source, out, duration_sec=dur):
            return None
        return out

    result = await asyncio.to_thread(_render)
    if result is None:
        logger.warning("cast: монтаж не собрался — остаёмся на стоке")
        return None
    logger.info(
        "cast montage ok кадров=%s сцена=%.1f с длительность=%.0f с тема=%r",
        len(frames), scene_sec, dur, topic,
    )
    return result
