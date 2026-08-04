"""Сток B-roll: несколько клипов Pexels + опционально картинки OpenAI → монтаж."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import random
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence

import httpx

logger = logging.getLogger(__name__)

_PEXELS_SEARCH = "https://api.pexels.com/videos/search"
_FULL_W = 1024
_FULL_H = 576


def _pexels_key() -> str:
    return (os.getenv("PEXELS_API_KEY") or os.getenv("YT_PRAYER_PEXELS_API_KEY") or "").strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or str(default)).strip())
    except ValueError:
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "on"}


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _probe_dur(path: Path) -> float:
    from youtube_prayer.audio_pipeline import probe_duration_sec

    return float(probe_duration_sec(path) or 0.0)


def _normalize_clip(src: Path, dest: Path, *, seconds: float, width: int, height: int) -> bool:
    """Привести клип к фиксированному кадру без растягивания (crop + setsar=1)."""
    ffmpeg = _ffmpeg()
    sec = max(2.0, float(seconds))
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"fps=25,setsar=1,format=yuv420p"
    )
    cmd = [
        ffmpeg,
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        str(src),
        "-t",
        f"{sec:.3f}",
        "-an",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
    return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 1000


def _image_ken_burns(img: Path, dest: Path, *, seconds: float, width: int, height: int) -> bool:
    ffmpeg = _ffmpeg()
    sec = max(3.0, float(seconds))
    frames = max(75, int(sec * 25))
    # медленный zoom; scale/crop держат 16:9
    vf = (
        f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
        f"crop={width * 2}:{height * 2},"
        f"zoompan=z='min(zoom+0.0004,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={frames}:s={width}x{height}:fps=25,"
        f"setsar=1,format=yuv420p"
    )
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
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
    return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 1000


def _concat_normalized(clips: Sequence[Path], dest: Path) -> bool:
    if not clips:
        return False
    ffmpeg = _ffmpeg()
    lst = dest.with_suffix(".txt")
    lines = []
    for c in clips:
        # concat demuxer: escape single quotes
        p = c.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{p}'")
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(lst),
        "-c",
        "copy",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
    if proc.returncode != 0 or not dest.is_file():
        # re-encode fallback
        cmd2 = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lst),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(dest),
        ]
        proc2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=300, check=False)
        return proc2.returncode == 0 and dest.is_file()
    return True


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
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
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
        "eq=brightness=0.03:saturation=0.85,fps=25,setsar=1,format=yuv420p",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "24",
        "-pix_fmt",
        "yuv420p",
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
    # несколько запросов с вариациями
    queries = [
        query or "calm nature",
        f"{query} sky" if query else "soft clouds sky",
        f"{query} ocean" if query else "calm ocean",
        "peaceful forest light",
        "candle warm light",
        "mountain sunrise fog",
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
                        "per_page": 12,
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
                        key=lambda f: abs(int(f.get("width") or 0) - 1280),
                    )
                    for f in files:
                        link = (f.get("link") or "").strip()
                        w = int(f.get("width") or 0)
                        if not link or w < 640:
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


async def _openai_images(
    prompt: str,
    dest_dir: Path,
    *,
    count: int,
) -> List[Path]:
    if not _env_flag("YT_PRAYER_IMAGE_GEN", True):
        return []
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key or count <= 0:
        return []
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        from openai import AsyncOpenAI
    except ImportError:
        return []

    client = AsyncOpenAI(api_key=key)
    model = (os.getenv("YT_PRAYER_IMAGE_MODEL") or "gpt-image-1").strip()
    base_prompt = (
        "Cinematic still for a calm Christian prayer video background, "
        "no text, no faces close-up, no logos, soft natural light, peaceful mood. "
        f"Theme: {prompt}"
    )
    out: List[Path] = []
    for i in range(count):
        try:
            # gpt-image-1 / dall-e-3
            kwargs = {
                "model": model,
                "prompt": base_prompt + f" Variation {i + 1}.",
                "n": 1,
                "size": "1536x1024",
            }
            if model.startswith("dall-e"):
                kwargs["size"] = "1792x1024"
                kwargs["response_format"] = "b64_json"
            resp = await client.images.generate(**kwargs)
            item = resp.data[0]
            raw_b64 = getattr(item, "b64_json", None)
            url = getattr(item, "url", None)
            path = dest_dir / f"openai_img_{i + 1}.png"
            if raw_b64:
                path.write_bytes(base64.b64decode(raw_b64))
            elif url:
                async with httpx.AsyncClient(timeout=90.0) as http:
                    r = await http.get(url)
                    r.raise_for_status()
                    path.write_bytes(r.content)
            else:
                continue
            out.append(path)
            logger.info("OpenAI image saved %s", path.name)
        except Exception as e:
            logger.warning("OpenAI image gen failed (%s): %s", model, e)
            # fallback dall-e-3 once
            if model != "dall-e-3":
                try:
                    resp = await client.images.generate(
                        model="dall-e-3",
                        prompt=base_prompt,
                        n=1,
                        size="1792x1024",
                        response_format="b64_json",
                    )
                    b64 = resp.data[0].b64_json
                    path = dest_dir / f"openai_img_{i + 1}.png"
                    path.write_bytes(base64.b64decode(b64))
                    out.append(path)
                except Exception as e2:
                    logger.warning("dall-e-3 fallback failed: %s", e2)
            break
    return out


async def build_broll_montage(
    work_dir: Path,
    *,
    query: str,
    duration_sec: float,
    width: int = _FULL_W,
    height: int = _FULL_H,
) -> Path:
    """
    Несколько сцен (Pexels + опц. OpenAI Ken Burns) → один ролик длиной duration_sec.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    scene_sec = float(_env_int("YT_PRAYER_SCENE_SEC", 10))
    scene_sec = max(5.0, min(20.0, scene_sec))
    need_scenes = max(3, int(duration_sec / scene_sec) + 1)
    pexels_n = _env_int("YT_PRAYER_BROLL_CLIPS", 5)
    img_n = _env_int("YT_PRAYER_IMAGE_COUNT", 2)

    raw_dir = work_dir / "broll_raw"
    norm_dir = work_dir / "broll_norm"
    raw_dir.mkdir(exist_ok=True)
    norm_dir.mkdir(exist_ok=True)

    videos = await _download_pexels_many(query, raw_dir, count=pexels_n)
    images = await _openai_images(query, raw_dir / "images", count=img_n)

    normalized: List[Path] = []
    # чередуем video / image
    v_i = 0
    im_i = 0
    colors = ["0x1a2744", "0x243b55", "0x2d4a6f", "0x1e3a2f", "0x3a2f1e"]
    for si in range(need_scenes):
        dest = norm_dir / f"scene_{si:02d}.mp4"
        ok = False
        # предпочитаем чередование
        prefer_img = images and (si % 3 == 2)
        if prefer_img and im_i < len(images):
            ok = await asyncio.to_thread(
                _image_ken_burns,
                images[im_i],
                dest,
                seconds=scene_sec,
                width=width,
                height=height,
            )
            im_i += 1
        if not ok and v_i < len(videos):
            ok = await asyncio.to_thread(
                _normalize_clip,
                videos[v_i % len(videos)],
                dest,
                seconds=scene_sec,
                width=width,
                height=height,
            )
            v_i += 1
        if not ok and im_i < len(images):
            ok = await asyncio.to_thread(
                _image_ken_burns,
                images[im_i],
                dest,
                seconds=scene_sec,
                width=width,
                height=height,
            )
            im_i += 1
        if not ok:
            ok = await asyncio.to_thread(
                _lavfi_scene,
                dest,
                seconds=scene_sec,
                width=width,
                height=height,
                color=colors[si % len(colors)],
            )
        if ok:
            normalized.append(dest)

    if not normalized:
        raise RuntimeError("не удалось собрать ни одной b-roll сцены")

    concat_path = work_dir / "broll_concat.mp4"
    ok = await asyncio.to_thread(_concat_normalized, normalized, concat_path)
    if not ok:
        raise RuntimeError("concat b-roll failed")

    final = work_dir / "broll_montage.mp4"
    ok = await asyncio.to_thread(
        _trim_or_loop_to_duration, concat_path, final, duration_sec=duration_sec
    )
    if not ok:
        raise RuntimeError("trim b-roll montage failed")
    logger.info(
        "broll montage ok scenes=%s dur≈%.1f bytes=%s",
        len(normalized),
        duration_sec,
        final.stat().st_size,
    )
    return final


# backward-compat
async def obtain_broll(
    work_dir: Path,
    *,
    query: str,
    duration_sec: float,
) -> Path:
    return await build_broll_montage(
        work_dir, query=query, duration_sec=duration_sec
    )
