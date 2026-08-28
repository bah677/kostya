"""Обложка 16:9: AI-фон + триггерный заголовок (тот же, что название видео)."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_FULL_W, _FULL_H = 1280, 720
_SHORT_W, _SHORT_H = 1080, 1920
_GEN_ATTEMPTS = 2


@dataclass(frozen=True)
class CoverPack:
    title: str
    thumbnail_title: str
    horizontal: Path
    vertical: Optional[Path] = None


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _find_font() -> str:
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    ):
        if Path(p).is_file():
            return p
    return "DejaVu Sans"


async def _gen_cover_bg(
    *,
    trend: str,
    brief: str,
    thumbnail_title: str,
    broll_query: str,
    dest: Path,
    size: str = "1536x1024",
) -> bool:
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        logger.error("OPENAI_API_KEY missing — cover AI generation impossible")
        return False
    try:
        from openai import AsyncOpenAI
    except ImportError:
        logger.error("openai package missing for cover generation")
        return False

    client = AsyncOpenAI(api_key=key)
    model = (os.getenv("YT_PRAYER_IMAGE_MODEL") or "gpt-image-1").strip()
    theme = broll_query or trend
    mood = (
        "Ultra eye-catching YouTube thumbnail background for a Christian prayer video. "
        "Cinematic dramatic lighting, high contrast, emotional spiritual atmosphere, "
        "golden divine light, strong focal point, professional clickbait thumbnail style. "
        "NO text, NO letters, NO logos, NO watermark, NO readable words, NO faces close-up. "
        f"Visual mood for topic: {trend}. "
        f"Prayer context: {brief}. "
        f"Emotional hook (do NOT render as text): {thumbnail_title}. "
        f"Scene mood keywords: {theme}"
    )
    try:
        kwargs = {"model": model, "prompt": mood, "n": 1, "size": size}
        if model.startswith("dall-e"):
            kwargs["size"] = "1792x1024" if size == "1536x1024" else "1024x1792"
            kwargs["response_format"] = "b64_json"
        resp = await client.images.generate(**kwargs)
        item = resp.data[0]
        b64 = getattr(item, "b64_json", None)
        url = getattr(item, "url", None)
        if b64:
            dest.write_bytes(base64.b64decode(b64))
            return True
        if url:
            async with httpx.AsyncClient(timeout=90.0) as http:
                r = await http.get(url)
                r.raise_for_status()
                dest.write_bytes(r.content)
            return True
    except Exception as e:
        logger.warning("cover bg gen failed model=%s: %s", model, e)
        if not model.startswith("dall-e"):
            try:
                resp = await client.images.generate(
                    model="dall-e-3",
                    prompt=mood,
                    n=1,
                    size="1024x1792" if size != "1536x1024" else "1792x1024",
                    response_format="b64_json",
                )
                dest.write_bytes(base64.b64decode(resp.data[0].b64_json))
                return True
            except Exception as e2:
                logger.warning("cover dall-e-3 failed: %s", e2)
    return False


def _wrap_title(title: str, *, max_chars: int, max_lines: int = 3) -> str:
    words = title.split()
    lines: list[str] = []
    cur: list[str] = []
    n = 0
    for w in words:
        add = len(w) + (1 if cur else 0)
        if cur and n + add > max_chars:
            lines.append(" ".join(cur))
            cur = [w]
            n = len(w)
            if len(lines) >= max_lines:
                break
        else:
            cur.append(w)
            n += add
    if cur and len(lines) < max_lines:
        lines.append(" ".join(cur))
    return "\n".join(lines[:max_lines])


def _burn_title(
    bg: Path,
    dest: Path,
    *,
    title: str,
    width: int,
    height: int,
    fontsize: int,
    max_chars: int,
    max_lines: int = 3,
) -> bool:
    ffmpeg = _ffmpeg()
    font = _find_font()
    wrapped = _wrap_title(title, max_chars=max_chars, max_lines=max_lines)
    text_path = dest.with_suffix(".txt")
    text_path.write_text(wrapped + "\n", encoding="utf-8")
    font_esc = font.replace("\\", "/").replace(":", "\\:")
    text_esc = text_path.resolve().as_posix().replace("\\", "/").replace(":", "\\:")
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"drawbox=x=0:y=ih*0.45:w=iw:h=ih*0.55:color=black@0.55:t=fill,"
        f"drawtext=fontfile='{font_esc}':textfile='{text_esc}':"
        f"fontsize={fontsize}:fontcolor=white:borderw=4:bordercolor=black@0.7:"
        f"line_spacing=14:"
        f"x=(w-text_w)/2:y=h*0.56"
    )
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(bg),
        "-vf",
        vf,
        "-frames:v",
        "1",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
    if proc.returncode != 0 or not dest.is_file():
        logger.warning("burn title failed: %s", (proc.stderr or "")[-400:])
        return False
    return True


async def generate_cover_pack(
    work_dir: Path,
    *,
    title: str,
    thumbnail_title: str,
    trend: str,
    brief: str = "",
    broll_query: str = "",
) -> Optional[CoverPack]:
    """AI-обложка 16:9 + кликбейтный заголовок на изображении."""
    work_dir.mkdir(parents=True, exist_ok=True)
    video_title = (title or "").strip()
    thumb_title = (thumbnail_title or video_title).strip()
    if len(thumb_title) < 3:
        logger.error("cover: empty thumbnail title")
        return None
    (work_dir / "cover_title.txt").write_text(thumb_title + "\n", encoding="utf-8")

    bg_h = work_dir / "cover_bg_h.png"
    ok_h = False
    for attempt in range(1, _GEN_ATTEMPTS + 1):
        ok_h = await _gen_cover_bg(
            trend=trend,
            brief=brief,
            thumbnail_title=thumb_title,
            broll_query=broll_query,
            dest=bg_h,
        )
        if ok_h:
            break
        logger.warning("cover AI attempt %s/%s failed", attempt, _GEN_ATTEMPTS)
        await asyncio.sleep(0.8 * attempt)
    if not ok_h:
        logger.error("cover AI generation failed after %s attempts", _GEN_ATTEMPTS)
        return None

    out_h = work_dir / "cover_16x9.jpg"
    ok = await asyncio.to_thread(
        _burn_title,
        bg_h,
        out_h,
        title=thumb_title,
        width=_FULL_W,
        height=_FULL_H,
        fontsize=72,
        max_chars=18,
        max_lines=3,
    )
    if not (ok and out_h.is_file()):
        logger.warning("cover pack incomplete title=%r", thumb_title)
        return None
    logger.info("cover ok thumb=%r yt_title=%r", thumb_title, video_title)
    return CoverPack(
        title=video_title,
        thumbnail_title=thumb_title,
        horizontal=out_h,
    )


async def generate_vertical_cover_pack(
    work_dir: Path,
    *,
    title: str,
    thumbnail_title: str,
    trend: str,
    brief: str = "",
    broll_query: str = "",
) -> Optional[CoverPack]:
    """AI-обложка 9:16 для YouTube Shorts."""
    work_dir.mkdir(parents=True, exist_ok=True)
    video_title = (title or "").strip()
    thumb_title = (thumbnail_title or video_title).strip()
    if len(thumb_title) < 3:
        logger.error("vertical cover: empty thumbnail title")
        return None
    (work_dir / "cover_title_vertical.txt").write_text(thumb_title + "\n", encoding="utf-8")

    bg_v = work_dir / "cover_bg_v.png"
    ok_v = False
    for attempt in range(1, _GEN_ATTEMPTS + 1):
        ok_v = await _gen_cover_bg(
            trend=trend,
            brief=brief,
            thumbnail_title=thumb_title,
            broll_query=broll_query,
            dest=bg_v,
            size="1024x1536",
        )
        if ok_v:
            break
        logger.warning("vertical cover AI attempt %s/%s failed", attempt, _GEN_ATTEMPTS)
        await asyncio.sleep(0.8 * attempt)
    if not ok_v:
        logger.error("vertical cover AI failed after %s attempts", _GEN_ATTEMPTS)
        return None

    out_v = work_dir / "cover_9x16.jpg"
    ok = await asyncio.to_thread(
        _burn_title,
        bg_v,
        out_v,
        title=thumb_title,
        width=_SHORT_W,
        height=_SHORT_H,
        fontsize=64,
        max_chars=16,
        max_lines=4,
    )
    if not (ok and out_v.is_file()):
        logger.warning("vertical cover burn failed title=%r", thumb_title)
        return None
    logger.info("vertical cover ok thumb=%r", thumb_title)
    return CoverPack(
        title=video_title,
        thumbnail_title=thumb_title,
        horizontal=out_v,
        vertical=out_v,
    )
