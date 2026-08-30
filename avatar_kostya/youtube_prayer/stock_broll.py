"""Сток B-roll: пул сцен + motion + xfade + киногрейд."""

from __future__ import annotations

import asyncio
import base64
import logging
import math
import os
import random
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence

import httpx

logger = logging.getLogger(__name__)

_PEXELS_SEARCH = "https://api.pexels.com/videos/search"
_FULL_W = 1920
_FULL_H = 1080
_ENCODE_PRESET = "medium"
_ENCODE_CRF = "20"
_XFADE_SEC = 0.8
_FPS = 25


def _pexels_key() -> str:
    return (os.getenv("PEXELS_API_KEY") or os.getenv("YT_PRAYER_PEXELS_API_KEY") or "").strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or str(default)).strip())
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or str(default)).strip())
    except ValueError:
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "on"}


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _ffprobe() -> str:
    return shutil.which("ffprobe") or "ffprobe"


def _probe_duration(path: Path) -> float:
    cmd = [
        _ffprobe(),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    try:
        return max(0.0, float((proc.stdout or "").strip()))
    except ValueError:
        return 0.0


def _encode_args() -> List[str]:
    return [
        "-c:v",
        "libx264",
        "-preset",
        _ENCODE_PRESET,
        "-crf",
        _ENCODE_CRF,
        "-pix_fmt",
        "yuv420p",
    ]


def _motion_zoompan(
    *,
    width: int,
    height: int,
    frames: int,
    direction: int,
) -> str:
    """Медленный zoom/pan; direction чередует паттерны."""
    d = direction % 4
    n = max(1, frames - 1)
    if d == 0:  # zoom in
        z = "min(1.0+on*0.00038,1.12)"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif d == 1:  # zoom out
        z = f"max(1.12-on*0.00038,1.0)"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif d == 2:  # pan right + slight zoom
        z = "1.10"
        x = f"(iw-iw/zoom)*on/{n}"
        y = "ih/2-(ih/zoom/2)"
    else:  # pan left + slight zoom
        z = "1.10"
        x = f"(iw-iw/zoom)*(1-on/{n})"
        y = "(ih-ih/zoom)*0.35"
    return (
        f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
        f"crop={width * 2}:{height * 2},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={_FPS}"
    )


def _normalize_clip(
    src: Path,
    dest: Path,
    *,
    seconds: float,
    width: int,
    height: int,
    start_sec: float = 0.0,
    direction: int = 0,
) -> bool:
    """Клип с Ken Burns / pan (видео тоже движется)."""
    ffmpeg = _ffmpeg()
    sec = max(2.0, float(seconds))
    ss = max(0.0, float(start_sec))
    frames = max(50, int(round(sec * _FPS)))
    motion = _motion_zoompan(
        width=width, height=height, frames=frames, direction=direction
    )
    vf = f"{motion},setsar=1,format=yuv420p"
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        f"{ss:.3f}",
        "-stream_loop",
        "-1",
        "-i",
        str(src),
        "-t",
        f"{sec:.3f}",
        "-an",
        "-vf",
        vf,
        *_encode_args(),
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240, check=False)
    if proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 1000:
        return True
    # fallback без zoompan
    vf2 = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},fps={_FPS},setsar=1,format=yuv420p"
    )
    cmd[cmd.index("-vf") + 1] = vf2
    proc2 = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
    return proc2.returncode == 0 and dest.is_file() and dest.stat().st_size > 1000


def _image_ken_burns(
    img: Path,
    dest: Path,
    *,
    seconds: float,
    width: int,
    height: int,
    direction: int = 0,
) -> bool:
    ffmpeg = _ffmpeg()
    sec = max(3.0, float(seconds))
    frames = max(75, int(sec * _FPS))
    motion = _motion_zoompan(
        width=width, height=height, frames=frames, direction=direction
    )
    vf = f"{motion},setsar=1,format=yuv420p"
    cmd = [
        ffmpeg,
        "-y",
        "-loop",
        "1",
        "-i",
        str(img),
        "-t",
        f"{sec:.3f}",
        "-vf",
        vf,
        "-an",
        *_encode_args(),
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
    return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 1000


def _xfade_concat(
    clips: Sequence[Path],
    dest: Path,
    *,
    fade_sec: float = _XFADE_SEC,
) -> bool:
    """Полный проход с xfade между сценами (не -c copy)."""
    if not clips:
        return False
    ffmpeg = _ffmpeg()
    fade = max(0.4, min(1.2, float(fade_sec)))
    if len(clips) == 1:
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(clips[0]),
            "-an",
            *_encode_args(),
            str(dest),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
        return proc.returncode == 0 and dest.is_file()

    # Попарный xfade во временные файлы — стабильнее длинного filter_complex.
    tmp_dir = dest.parent / "_xfade_tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    current = clips[0]
    try:
        for i, nxt in enumerate(clips[1:], 1):
            dur = _probe_duration(current)
            if dur <= fade + 0.2:
                # слишком короткий — жёсткая склейка
                offset = max(0.1, dur * 0.5)
            else:
                offset = dur - fade
            out = tmp_dir / f"xf_{i:03d}.mp4"
            fc = (
                f"[0:v][1:v]xfade=transition=fade:duration={fade:.3f}:"
                f"offset={offset:.3f},format=yuv420p[v]"
            )
            cmd = [
                ffmpeg,
                "-y",
                "-i",
                str(current),
                "-i",
                str(nxt),
                "-filter_complex",
                fc,
                "-map",
                "[v]",
                "-an",
                *_encode_args(),
                str(out),
            ]
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600, check=False
            )
            if proc.returncode != 0 or not out.is_file():
                logger.warning(
                    "xfade step %s failed: %s", i, (proc.stderr or "")[-350:]
                )
                return False
            current = out
        shutil.copy2(current, dest)
        return dest.is_file()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _apply_cinematic_grade(src: Path, dest: Path) -> bool:
    ffmpeg = _ffmpeg()
    fc_full = (
        "[0:v]split[base][glow];"
        "[glow]eq=brightness=0.06:saturation=0.7,gblur=sigma=14[glow2];"
        "[base][glow2]blend=all_mode=screen:all_opacity=0.18[m];"
        "[m]curves=r='0/0.04 0.5/0.55 1/1':g='0/0.02 0.5/0.52 1/1':b='0/0 0.5/0.48 1/0.96',"
        "eq=saturation=0.86:contrast=1.05,"
        "vignette=PI/5,"
        "noise=alls=6:allf=t,"
        "setsar=1,format=yuv420p[vout]"
    )
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(src),
        "-filter_complex",
        fc_full,
        "-map",
        "[vout]",
        "-an",
        *_encode_args(),
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, check=False)
    if proc.returncode == 0 and dest.is_file():
        return True
    logger.warning("cinematic grade failed, copy plain: %s", (proc.stderr or "")[-300:])
    try:
        shutil.copy2(src, dest)
        return dest.is_file()
    except Exception:
        return False


def _trim_or_loop_to_duration(src: Path, dest: Path, *, duration_sec: float) -> bool:
    ffmpeg = _ffmpeg()
    dur = max(10.0, float(duration_sec))
    cmd = [
        ffmpeg,
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        str(src),
        "-t",
        f"{dur:.3f}",
        "-an",
        *_encode_args(),
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, check=False)
    return proc.returncode == 0 and dest.is_file()


def _lavfi_scene(dest: Path, *, seconds: float, width: int, height: int, color: str) -> bool:
    ffmpeg = _ffmpeg()
    sec = max(2.0, float(seconds))
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={width}x{height}:d={sec:.2f}",
        "-vf",
        f"eq=brightness=0.03:saturation=0.85,fps={_FPS},setsar=1,format=yuv420p",
        "-an",
        *_encode_args(),
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    return proc.returncode == 0 and dest.is_file()


async def _download_pexels_many(
    query: str,
    dest_dir: Path,
    *,
    count: int,
) -> List[Path]:
    key = _pexels_key()
    if not key:
        return []
    dest_dir.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(90.0, connect=15.0)
    headers = {"Authorization": key}
    queries = [
        query or "calm nature",
        f"{query} sky" if query else "soft clouds sky",
        f"{query} ocean" if query else "calm ocean",
        f"{query} forest" if query else "peaceful forest light",
        "candle warm light prayer",
        "mountain sunrise fog",
        "soft rain window cozy",
        "golden hour field wind",
        "church light stained glass soft",
        "lake reflection dawn mist",
        "desert sunset calm",
        "snow forest quiet",
    ]
    out: List[Path] = []
    seen_ids: set[int] = set()
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            for q in queries:
                if len(out) >= count:
                    break
                r = await client.get(
                    _PEXELS_SEARCH,
                    params={
                        "query": q,
                        "per_page": 15,
                        "orientation": "landscape",
                        "size": "medium",
                    },
                )
                if r.status_code >= 400:
                    logger.warning("Pexels search HTTP %s q=%r", r.status_code, q)
                    continue
                videos = list(r.json().get("videos") or [])
                random.shuffle(videos)
                for vid in videos:
                    if len(out) >= count:
                        break
                    vid_id = int(vid.get("id") or 0)
                    if vid_id and vid_id in seen_ids:
                        continue
                    files = sorted(
                        vid.get("video_files") or [],
                        key=lambda f: abs(int(f.get("width") or 0) - 1920),
                    )
                    for f in files:
                        link = (f.get("link") or "").strip()
                        w = int(f.get("width") or 0)
                        if not link or w < 1280:
                            continue
                        dr = await client.get(link)
                        if dr.status_code >= 400 or len(dr.content) < 50_000:
                            continue
                        if vid_id:
                            seen_ids.add(vid_id)
                        path = dest_dir / f"pexels_{vid_id or random.randint(1000,9999)}_{len(out)}.mp4"
                        path.write_bytes(dr.content)
                        out.append(path)
                        logger.info("Pexels clip #%s q=%r bytes=%s", len(out), q, len(dr.content))
                        break
    except Exception as e:
        logger.warning("Pexels multi download failed: %s", e)
    return out


async def _openai_one_image(
    prompt: str,
    dest: Path,
    *,
    client=None,
    model: str = "",
) -> Optional[Path]:
    """Один кадр gpt-image-1 / dall-e под конкретный visual-промпт."""
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        return None
    try:
        from openai import AsyncOpenAI
    except ImportError:
        return None
    cli = client or AsyncOpenAI(api_key=key)
    model = (model or os.getenv("YT_PRAYER_IMAGE_MODEL") or "gpt-image-1").strip()
    full = (
        "Cinematic still for a calm Christian prayer video, 16:9 composition, "
        "no text, no letters, no logos, no watermark, no faces close-up, "
        "soft natural light, film still quality. "
        f"{prompt}"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        kwargs = {"model": model, "prompt": full, "n": 1, "size": "1536x1024"}
        if model.startswith("dall-e-2"):
            kwargs["response_format"] = "b64_json"
        resp = await cli.images.generate(**kwargs)
        item = resp.data[0]
        raw_b64 = getattr(item, "b64_json", None)
        url = getattr(item, "url", None)
        if raw_b64:
            dest.write_bytes(base64.b64decode(raw_b64))
        elif url:
            async with httpx.AsyncClient(timeout=90.0) as http:
                r = await http.get(url)
                r.raise_for_status()
                dest.write_bytes(r.content)
        else:
            return None
        return dest if dest.is_file() and dest.stat().st_size > 500 else None
    except Exception as e:
        logger.warning("OpenAI image failed (%s): %s", model, e)
        if model.startswith("dall-e"):
            return None
        try:
            resp = await cli.images.generate(
                model="dall-e-3",
                prompt=full,
                n=1,
                size="1792x1024",
            )
            item = resp.data[0]
            raw_b64 = getattr(item, "b64_json", None)
            url = getattr(item, "url", None)
            if raw_b64:
                dest.write_bytes(base64.b64decode(raw_b64))
            elif url:
                async with httpx.AsyncClient(timeout=90.0) as http:
                    r = await http.get(url)
                    r.raise_for_status()
                    dest.write_bytes(r.content)
            else:
                return None
            return dest if dest.is_file() and dest.stat().st_size > 500 else None
        except Exception as e2:
            logger.warning("dall-e-3 fallback failed: %s", e2)
            return None


async def _openai_images(
    prompt: str,
    dest_dir: Path,
    *,
    count: int,
) -> List[Path]:
    if not _env_flag("YT_PRAYER_IMAGE_GEN", True):
        return []
    if count <= 0:
        return []
    dest_dir.mkdir(parents=True, exist_ok=True)
    out: List[Path] = []
    for i in range(count):
        path = dest_dir / f"openai_img_{i + 1}.png"
        got = await _openai_one_image(
            f"Theme: {prompt}. Variation {i + 1}.",
            path,
        )
        if got:
            out.append(got)
            logger.info("OpenAI image saved %s", got.name)
        else:
            break
    return out


async def _build_ai_anchor_scenes(
    blocks: Sequence,
    *,
    dest_dir: Path,
    scene_sec: float,
    width: int,
    height: int,
    max_anchors: int,
) -> List[Optional[Path]]:
    """
    Гибрид уровень 2: gpt-image-1 кадр на смысловой пик → Ken Burns (image-to-video).
    Возвращает список длины len(blocks): Path | None по индексу блока.
    """
    from youtube_prayer.scene_plan import SemanticBlock

    dest_dir.mkdir(parents=True, exist_ok=True)
    result: List[Optional[Path]] = [None] * len(blocks)
    if not _env_flag("YT_PRAYER_AI_ANCHORS", True):
        return result
    if not _env_flag("YT_PRAYER_IMAGE_GEN", True):
        return result

    anchor_idxs = [
        i
        for i, b in enumerate(blocks)
        if isinstance(b, SemanticBlock) and b.is_anchor
    ]
    if not anchor_idxs:
        anchor_idxs = list(range(len(blocks)))
    max_n = max(6, min(10, int(max_anchors)))
    if len(anchor_idxs) > max_n:
        step = len(anchor_idxs) / max_n
        picked = [anchor_idxs[int(i * step)] for i in range(max_n)]
        anchor_idxs = sorted(set(picked))

    for n, bi in enumerate(anchor_idxs):
        b = blocks[bi]
        visual = getattr(b, "visual", "") or str(b)
        label = getattr(b, "label", f"a{bi}")
        img = dest_dir / f"anchor_{bi:02d}.png"
        got = await _openai_one_image(visual, img)
        if not got:
            logger.warning("AI anchor image failed block=%s label=%r", bi, label)
            continue
        clip = dest_dir / f"anchor_{bi:02d}.mp4"
        ok = await asyncio.to_thread(
            _image_ken_burns,
            got,
            clip,
            seconds=scene_sec,
            width=width,
            height=height,
            direction=n,
        )
        if ok and clip.is_file():
            result[bi] = clip
            logger.info("AI anchor scene ok i=%s label=%r", bi, label)
        else:
            logger.warning("AI anchor kenburns failed i=%s", bi)
    logger.info(
        "AI anchors ready %s/%s",
        sum(1 for p in result if p),
        len(anchor_idxs),
    )
    return result


async def _build_scene_pool(
    *,
    videos: Sequence[Path],
    images: Sequence[Path],
    pool_dir: Path,
    pool_size: int,
    scene_sec: float,
    width: int,
    height: int,
) -> List[Path]:
    """Пул нормализованных сцен с motion (zoom/pan) на видео и картинках."""
    pool_dir.mkdir(parents=True, exist_ok=True)
    pool: List[Path] = []
    if not videos and not images:
        colors = ["0x1a2744", "0x243b55", "0x2d4a6f", "0x1e3a2f"]
        for i in range(min(4, pool_size)):
            dest = pool_dir / f"scene_{i:02d}.mp4"
            ok = await asyncio.to_thread(
                _lavfi_scene,
                dest,
                seconds=scene_sec,
                width=width,
                height=height,
                color=colors[i % len(colors)],
            )
            if ok:
                pool.append(dest)
        return pool

    media_cycle: List[tuple[str, Path]] = []
    for v in videos:
        media_cycle.append(("video", v))
    for im in images:
        media_cycle.append(("image", im))
    if not media_cycle:
        return pool

    for i in range(pool_size):
        dest = pool_dir / f"scene_{i:02d}.mp4"
        kind, src = media_cycle[i % len(media_cycle)]
        ok = False
        if kind == "image":
            ok = await asyncio.to_thread(
                _image_ken_burns,
                src,
                dest,
                seconds=scene_sec,
                width=width,
                height=height,
                direction=i,
            )
        else:
            start = float((i * 7) % 40)
            ok = await asyncio.to_thread(
                _normalize_clip,
                src,
                dest,
                seconds=scene_sec,
                width=width,
                height=height,
                start_sec=start,
                direction=i,
            )
            if not ok:
                ok = await asyncio.to_thread(
                    _normalize_clip,
                    src,
                    dest,
                    seconds=scene_sec,
                    width=width,
                    height=height,
                    start_sec=0.0,
                    direction=i + 1,
                )
        if ok:
            pool.append(dest)
        else:
            logger.warning("scene pool item failed i=%s src=%s", i, src.name)
    return pool


async def build_broll_montage(
    work_dir: Path,
    *,
    query: str,
    duration_sec: float,
    width: int = _FULL_W,
    height: int = _FULL_H,
    prayer_text: str = "",
) -> Path:
    """
    Пул стока + AI-якоря на смысловые пики → xfade → киногрейд → trim.
    """
    from youtube_prayer.scene_plan import (
        expand_blocks_to_slots,
        plan_semantic_blocks,
        slot_block_indices,
    )

    work_dir.mkdir(parents=True, exist_ok=True)
    scene_sec = float(_env_int("YT_PRAYER_SCENE_SEC", 10))
    scene_sec = max(5.0, min(20.0, scene_sec))
    pool_size = _env_int("YT_PRAYER_SCENE_POOL", 12)
    pool_size = max(8, min(16, pool_size))
    pexels_n = max(pool_size, _env_int("YT_PRAYER_BROLL_CLIPS", 12))
    # generic AI stills только если якоря выключены
    img_n = _env_int("YT_PRAYER_IMAGE_COUNT", 3)
    if _env_flag("YT_PRAYER_AI_ANCHORS", True):
        img_n = _env_int("YT_PRAYER_IMAGE_COUNT", 0)
    fade = _env_float("YT_PRAYER_XFADE_SEC", _XFADE_SEC)
    fade = max(0.5, min(1.2, fade))
    max_anchors = _env_int("YT_PRAYER_AI_ANCHOR_COUNT", 8)
    max_anchors = max(6, min(10, max_anchors))

    raw_dir = work_dir / "broll_raw"
    pool_dir = work_dir / "broll_pool"
    anchor_dir = work_dir / "broll_anchors"
    raw_dir.mkdir(exist_ok=True)

    async def _complete(system: str, user: str) -> Optional[str]:
        try:
            from youtube_prayer.compose import deepseek_complete

            text, _ = await deepseek_complete(
                system, user, temperature=0.3, max_tokens=900
            )
            return text
        except Exception as e:
            logger.warning("scene plan complete failed: %s", e)
            return None

    blocks = await plan_semantic_blocks(
        prayer_text,
        pool_size=pool_size,
        complete_fn=_complete if prayer_text.strip() else None,
    )

    videos = await _download_pexels_many(query, raw_dir, count=pexels_n)
    images = await _openai_images(query, raw_dir / "images", count=img_n)
    logger.info(
        "broll sources videos=%s images=%s pool_size=%s scene_sec=%s fade=%.2f "
        "dur=%.1f anchors_max=%s blocks=%s",
        len(videos),
        len(images),
        pool_size,
        scene_sec,
        fade,
        duration_sec,
        max_anchors,
        len(blocks),
    )

    pool = await _build_scene_pool(
        videos=videos,
        images=images,
        pool_dir=pool_dir,
        pool_size=pool_size,
        scene_sec=scene_sec,
        width=width,
        height=height,
    )
    if not pool:
        raise RuntimeError("не удалось собрать пул b-roll сцен")

    anchors = await _build_ai_anchor_scenes(
        blocks,
        dest_dir=anchor_dir,
        scene_sec=scene_sec,
        width=width,
        height=height,
        max_anchors=max_anchors,
    )

    step = max(1.0, scene_sec - fade)
    need_scenes = max(1, int(math.ceil((float(duration_sec) + fade) / step)))
    stock_order = expand_blocks_to_slots(
        blocks, n_slots=need_scenes, pool_size=len(pool)
    )
    block_for_slot = slot_block_indices(blocks, n_slots=need_scenes)

    timeline: List[Path] = []
    ai_hits = 0
    for i in range(need_scenes):
        bi = block_for_slot[i]
        clip: Optional[Path] = None
        if 0 <= bi < len(anchors) and anchors[bi] is not None:
            clip = anchors[bi]
            ai_hits += 1
        if clip is None:
            clip = pool[stock_order[i] % len(pool)]
        # не дублировать подряд один и тот же файл
        if timeline and clip == timeline[-1] and len(pool) > 1:
            clip = pool[(stock_order[i] + 1) % len(pool)]
        timeline.append(clip)

    logger.info(
        "broll timeline scenes=%s pool=%s ai_slots=%s blocks=%s",
        need_scenes,
        len(pool),
        ai_hits,
        len(blocks),
    )

    concat_path = work_dir / "broll_concat.mp4"
    ok = await asyncio.to_thread(
        _xfade_concat, timeline, concat_path, fade_sec=fade
    )
    if not ok:
        raise RuntimeError("xfade b-roll failed")

    graded = work_dir / "broll_graded.mp4"
    ok = await asyncio.to_thread(_apply_cinematic_grade, concat_path, graded)
    if not ok:
        graded = concat_path

    final = work_dir / "broll_montage.mp4"
    ok = await asyncio.to_thread(
        _trim_or_loop_to_duration, graded, final, duration_sec=duration_sec
    )
    if not ok:
        raise RuntimeError("trim b-roll montage failed")
    logger.info(
        "broll montage ok pool=%s timeline=%s ai=%s dur≈%.1f bytes=%s",
        len(pool),
        need_scenes,
        ai_hits,
        duration_sec,
        final.stat().st_size,
    )
    return final


async def obtain_broll(
    work_dir: Path,
    *,
    query: str,
    duration_sec: float,
    prayer_text: str = "",
) -> Path:
    return await build_broll_montage(
        work_dir,
        query=query,
        duration_sec=duration_sec,
        prayer_text=prayer_text,
    )
