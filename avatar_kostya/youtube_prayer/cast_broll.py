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

from youtube_prayer.cast import DEFAULT_HEROINE, HEROINES, Scene, heroine, scenes_for_topic
from youtube_prayer.cast_bank import bank_dir, frames_for_scene

logger = logging.getLogger(__name__)

# Меньше четырёх кадров — это уже не видеоряд, а слайд-шоу.
_MIN_FRAMES = 4


def pick_frames(
    topic: str, *, count: int = 8, seed: Optional[int] = None, heroine_key: str = ""
) -> List[Path]:
    """Кадры под тему, по одному варианту на сцену, в сюжетном порядке."""
    rng = random.Random(seed)
    out: List[Path] = []
    for scene in scenes_for_topic(topic, count=count, heroine_key=heroine_key):
        variants = frames_for_scene(scene, heroine_key)
        if variants:
            out.append(rng.choice(variants))
    return out


def bank_ready(topic: str = "", *, count: int = 8, heroine_key: str = "") -> bool:
    return len(pick_frames(topic, count=count, heroine_key=heroine_key)) >= _MIN_FRAMES


def _clip_to_scene(
    src: Path, dest: Path, *, seconds: float, width: int, height: int
) -> bool:
    """Готовый клип под размер кадра и длину сцены.

    Наезд поверх не делаем: в клипе уже есть своё движение, и второй слой
    с ним дерётся — получается шевеление ради шевеления.
    """
    import subprocess

    from youtube_prayer.stock_broll import _FPS, _encode_args, _ffmpeg

    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},fps={_FPS},setsar=1,format=yuv420p"
    )
    cmd = [
        _ffmpeg(), "-y", "-i", str(src), "-t", f"{max(2.0, float(seconds)):.3f}",
        "-vf", vf, "-an", *_encode_args(), str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
    ok = proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 1000
    if not ok:
        logger.warning("cast: клип %s не лёг в сцену: %s", src.name, proc.stderr[-200:])
    return ok


def choose_heroine(topic: str = "", *, index: int = 1) -> str:
    """Какая героиня снимается в этом ролике.

    Выбор делается один раз на ролик: кадры идут подряд в одном сюжете, и
    смена лица посреди него читается как другой человек.

    Настройка «Героиня» задаёт, кто снимается: одна из двух или обе
    по очереди. Если у выбранной банк не набран, берём ту, у которой он
    есть — лучше знакомое лицо, чем сток.
    """
    from youtube_prayer.langs import setting

    want = (setting("YT_PRAYER_HEROINE", "") or "mix").strip().lower()
    keys = [h.key for h in HEROINES]
    if want in keys:
        order = [want] + [k for k in keys if k != want]
    else:
        # По очереди: нечётные ролики пачки одной, чётные другой.
        start = (max(1, int(index)) - 1) % len(keys)
        order = keys[start:] + keys[:start]
    for key in order:
        if bank_ready(topic, heroine_key=key):
            return key
    return order[0] if order else DEFAULT_HEROINE


async def build_cast_montage(
    work_dir: Path,
    *,
    topic: str,
    duration_sec: float,
    width: int,
    height: int,
    seed: Optional[int] = None,
    heroine_key: str = "",
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
    who = (heroine_key or "").strip() or DEFAULT_HEROINE
    frames = pick_frames(topic, count=16, seed=seed, heroine_key=who)
    if len(frames) < _MIN_FRAMES:
        logger.warning(
            "cast: у героини %s в банке %s кадров под тему %r — мало, остаёмся на стоке",
            who,
            len(frames),
            topic,
        )
        return None
    logger.info("cast: снимает %s, кадров %s", heroine(who).label, len(frames))

    # Оживлённые кадры: рядом с картинкой лежит mp4 на десять секунд.
    # Там, где клипа нет, остаётся наезд по картинке — банк может быть
    # оживлён наполовину, и это не повод падать на сток.
    movies = {f: f.with_suffix(".mp4") for f in frames}
    movies = {f: m for f, m in movies.items() if m.is_file() and m.stat().st_size > 50_000}
    if movies:
        logger.info("cast: оживлённых кадров %s из %s", len(movies), len(frames))

    # Длину сцены считаем от озвучки: лучше показать меньше кадров подольше,
    # чем гнать слайд-шоу. Но и держать один кадр дольше 12 секунд не стоит.
    fade = _env_float("YT_PRAYER_XFADE_SEC", 0.8)
    # Клип длится десять секунд, растягивать его нечем: петля разворотом
    # гонит пар обратно в кружку, а наплыв мельтешит. Поэтому когда кадры
    # оживлены, сцена не длиннее клипа.
    cap = 10.0 if len(movies) >= len(frames) // 2 else 12.0
    scene_sec = max(5.0, min(cap, dur / max(1, len(frames))))
    needed = max(_MIN_FRAMES, min(len(frames), int(dur / max(1.0, scene_sec - fade)) + 1))
    frames = frames[:needed]
    scene_sec = max(5.0, min(cap, dur / len(frames) + fade))

    work_dir.mkdir(parents=True, exist_ok=True)
    pool = work_dir / "cast_clips"
    pool.mkdir(parents=True, exist_ok=True)

    def _render() -> Optional[Path]:
        clips: List[Path] = []
        for i, img in enumerate(frames):
            dest = pool / f"{i:02d}.mp4"
            movie = movies.get(img)
            if movie is not None and _clip_to_scene(
                movie, dest, seconds=scene_sec, width=width, height=height
            ):
                clips.append(dest)
                continue
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
        "cast montage ok кадров=%s (оживлённых %s) сцена=%.1f с "
        "длительность=%.0f с тема=%r",
        len(frames), sum(1 for f in frames if f in movies), scene_sec, dur, topic,
    )
    return result
