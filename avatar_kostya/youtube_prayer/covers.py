"""Кликбейт-обложки 16:9 и 9:16: LLM-заголовок + картинка + текст поверх."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

_FULL_W, _FULL_H = 1280, 720
_SHORT_W, _SHORT_H = 1080, 1920


@dataclass(frozen=True)
class CoverPack:
    title: str
    horizontal: Path
    vertical: Path


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


async def _llm_clickbait_title(
    *,
    trend: str,
    brief: str,
    lang: str,
    complete_fn,
) -> str:
    lang = (lang or "ru").lower()
    if lang == "en":
        system = (
            "You write YouTube thumbnail titles for Christian prayer videos. "
            "Return STRICT JSON: {\"title\":\"...\"}. "
            "Title: 3–8 words, emotional clickbait but not blasphemous, no emoji, "
            "no quotes around the whole title. Example: When Anxiety Won't Let Go"
        )
        user = f"Trend: {trend}\nBrief: {brief}\nLanguage: English"
        fallback = "A Prayer When You're Exhausted"
    else:
        system = (
            "Ты пишешь кликбейт-заголовки для обложек YouTube-молитв. "
            "Ответ СТРОГО JSON: {\"title\":\"...\"}. "
            "Заголовок: 3–8 слов, цепляющий, но без кощунства и без эмодзи. "
            "Пример: Когда тревога не отпускает"
        )
        user = f"Тренд: {trend}\nБриф: {brief}\nЯзык: русский"
        fallback = "Молитва, когда нет сил"
    raw = await complete_fn(system, user)
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        title = str(data.get("title") or "").strip()
    except Exception:
        m = re.search(r'"title"\s*:\s*"([^"]+)"', text)
        title = m.group(1).strip() if m else ""
    title = re.sub(r"\s+", " ", title).strip(" «»\"'")
    if len(title) < 3:
        return fallback
    # не длиннее ~42 символов для читаемости на миниатюре
    if len(title) > 48:
        title = title[:45].rstrip() + "…"
    return title


async def _gen_cover_bg(prompt: str, dest: Path, *, landscape: bool) -> bool:
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        return False
    try:
        from openai import AsyncOpenAI
    except ImportError:
        return False
    client = AsyncOpenAI(api_key=key)
    model = (os.getenv("YT_PRAYER_IMAGE_MODEL") or "gpt-image-1").strip()
    mood = (
        "Cinematic YouTube thumbnail background for a Christian prayer video, "
        "dramatic soft light, emotional atmosphere, no text, no logos, no watermark, "
        "no readable letters. "
        f"Mood/theme: {prompt}"
    )
    try:
        size = "1536x1024" if landscape else "1024x1536"
        kwargs = {"model": model, "prompt": mood, "n": 1, "size": size}
        if model.startswith("dall-e"):
            kwargs["size"] = "1792x1024" if landscape else "1024x1792"
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
        logger.warning("cover bg gen failed: %s", e)
        if not model.startswith("dall-e"):
            try:
                size = "1792x1024" if landscape else "1024x1792"
                resp = await client.images.generate(
                    model="dall-e-3",
                    prompt=mood,
                    n=1,
                    size=size,
                    response_format="b64_json",
                )
                dest.write_bytes(base64.b64decode(resp.data[0].b64_json))
                return True
            except Exception as e2:
                logger.warning("cover dall-e-3 failed: %s", e2)
    return False


def _solid_bg(dest: Path, *, width: int, height: int, color: str = "0x152238") -> bool:
    ffmpeg = _ffmpeg()
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={width}x{height}:d=1",
        "-frames:v",
        "1",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    return proc.returncode == 0 and dest.is_file()


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
) -> bool:
    ffmpeg = _ffmpeg()
    font = _find_font()
    wrapped = _wrap_title(title, max_chars=max_chars)
    text_path = dest.with_suffix(".txt")
    text_path.write_text(wrapped + "\n", encoding="utf-8")
    font_esc = font.replace("\\", "/").replace(":", "\\:")
    text_esc = text_path.resolve().as_posix().replace("\\", "/").replace(":", "\\:")
    # тёмный градиент снизу + крупный белый текст из файла (multiline)
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
    trend: str,
    brief: str,
    lang: str,
    complete_fn,
    broll_query: str = "",
) -> Optional[CoverPack]:
    """Горизонтальная + вертикальная обложка с кликбейт-заголовком."""
    work_dir.mkdir(parents=True, exist_ok=True)
    title = await _llm_clickbait_title(
        trend=trend, brief=brief, lang=lang, complete_fn=complete_fn
    )
    (work_dir / "cover_title.txt").write_text(title, encoding="utf-8")

    theme = broll_query or trend
    bg_h = work_dir / "cover_bg_h.png"
    bg_v = work_dir / "cover_bg_v.png"
    ok_h = await _gen_cover_bg(theme, bg_h, landscape=True)
    ok_v = await _gen_cover_bg(theme, bg_v, landscape=False)
    if not ok_h:
        await asyncio.to_thread(_solid_bg, bg_h, width=_FULL_W, height=_FULL_H)
    if not ok_v:
        await asyncio.to_thread(_solid_bg, bg_v, width=_SHORT_W, height=_SHORT_H)

    out_h = work_dir / "cover_16x9.jpg"
    out_v = work_dir / "cover_9x16.jpg"
    ok1 = await asyncio.to_thread(
        _burn_title,
        bg_h,
        out_h,
        title=title,
        width=_FULL_W,
        height=_FULL_H,
        fontsize=64,
        max_chars=22,
    )
    ok2 = await asyncio.to_thread(
        _burn_title,
        bg_v,
        out_v,
        title=title,
        width=_SHORT_W,
        height=_SHORT_H,
        fontsize=72,
        max_chars=16,
    )
    if not (ok1 and ok2 and out_h.is_file() and out_v.is_file()):
        logger.warning("cover pack incomplete title=%r", title)
        return None
    logger.info("covers ok title=%r", title)
    return CoverPack(title=title, horizontal=out_h, vertical=out_v)
