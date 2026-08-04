"""Сток B-roll: Pexels (если есть ключ) или ffmpeg lavfi fallback."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_PEXELS_SEARCH = "https://api.pexels.com/videos/search"


def _pexels_key() -> str:
    return (os.getenv("PEXELS_API_KEY") or os.getenv("YT_PRAYER_PEXELS_API_KEY") or "").strip()


async def _download_pexels_video(query: str, dest: Path) -> Optional[Path]:
    key = _pexels_key()
    if not key:
        return None
    timeout = httpx.Timeout(60.0, connect=15.0)
    headers = {"Authorization": key}
    params = {
        "query": query or "calm nature",
        "per_page": 8,
        "orientation": "landscape",
        "size": "medium",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            r = await client.get(_PEXELS_SEARCH, params=params)
            if r.status_code >= 400:
                logger.warning("Pexels search HTTP %s", r.status_code)
                return None
            data = r.json()
            videos = data.get("videos") or []
            if not videos:
                return None
            random.shuffle(videos)
            for vid in videos[:5]:
                files = vid.get("video_files") or []
                files = sorted(
                    files,
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
                    dest.write_bytes(dr.content)
                    logger.info(
                        "Pexels broll query=%r bytes=%s w=%s",
                        query,
                        len(dr.content),
                        w,
                    )
                    return dest
    except Exception as e:
        logger.warning("Pexels download failed: %s", e)
    return None


def _render_lavfi_loop(dest: Path, *, duration_sec: float) -> Path:
    """Мягкий фон (fallback без стока)."""
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    dur = max(30.0, float(duration_sec) + 2.0)
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x1a2744:s=1280x720:d={dur:.2f}",
        "-vf",
        "eq=brightness=0.03:saturation=0.8,fps=25,format=yuv420p",
        "-t",
        f"{dur:.2f}",
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
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
    if proc.returncode != 0 or not dest.is_file():
        raise RuntimeError(f"lavfi fallback failed: {(proc.stderr or '')[-400:]}")
    return dest


async def obtain_broll(
    work_dir: Path,
    *,
    query: str,
    duration_sec: float,
) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    pexels_path = work_dir / "broll_src.mp4"
    got = await _download_pexels_video(query, pexels_path)
    if got and got.is_file():
        return got
    logger.info("broll fallback lavfi (no Pexels or empty) query=%r", query)
    out = work_dir / "broll_lavfi.mp4"
    return await asyncio.to_thread(_render_lavfi_loop, out, duration_sec=duration_sec)
