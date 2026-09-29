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
from typing import List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

_FULL_W, _FULL_H = 1280, 720
_SHORT_W, _SHORT_H = 1080, 1920
_GEN_ATTEMPTS = 2

# Вертикальная обложка: заголовок вверху, низ пустой — там YouTube рисует
# счётчик просмотров и служебные плашки.
_V_TITLE_SIZE = 185
_V_TITLE_MAX_CHARS = 11
_V_TITLE_MAX_LINES = 4
_V_TITLE_TOP_FRAC = 0.135
_V_TITLE_LEFT = 68
# Средняя ширина знака у Montserrat ExtraBold — замер по рендеру: 79 px при
# кегле 185. Нужна, чтобы поймать строку, которая вылезет за край кадра.
_V_CHAR_W_RATIO = 0.43
_V_TITLE_MIN_SIZE = 120
_V_ACCENT_W = 230
_V_ACCENT_H = 11
_V_ACCENT_GAP = 62
_V_BADGE_SIZE = 44
_V_BADGE_GAP = 54
_COL_HEAD = "&H00F4F8FF"    # ASS BGR: тёплый белый
_COL_GOLD = "&H005AB4E8"    # золото
_COL_BADGE = "&H00C8D2DC"

_COVER_VARIANT_MOODS = (
    "golden divine light, high contrast, emotional spiritual atmosphere",
    "blue hour mist, soft candle glow, intimate quiet prayer mood",
    "dramatic rim light, deep shadows, cinematic hope after hardship",
    "warm sunrise haze, open sky, peaceful breakthrough feeling",
)


@dataclass(frozen=True)
class CoverPack:
    title: str
    thumbnail_title: str
    horizontal: Path
    vertical: Optional[Path] = None
    variants: tuple = ()  # Path — доп. варианты 16:9 на выбор
    hook_question: str = ""


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _find_font() -> str:
    for p in (
        "/usr/share/fonts/truetype/noto/NotoSerifDisplay-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansDisplay-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ):
        if Path(p).is_file():
            return p
    return "Noto Serif Display"


async def _save_image_response_item(item, dest: Path) -> bool:
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
    return False


async def _gen_cover_bg(
    *,
    trend: str,
    brief: str,
    thumbnail_title: str,
    broll_query: str,
    dest: Path,
    size: str = "1536x1024",
    mood_extra: str = "",
    vertical: bool = False,
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
    mood_line = mood_extra or _COVER_VARIANT_MOODS[0]
    # Вертикаль: верх кадра должен остаться «пустым» — туда ляжет заголовок.
    layout = (
        "Vertical 9:16 composition for a phone screen. "
        "Keep the TOP 45% of the frame visually calm and uncluttered "
        "(open sky, soft light, haze or bokeh) — a large title goes there. "
        "Put the main subject and the strongest detail in the LOWER HALF. "
        if vertical
        else "Horizontal 16:9 composition. "
    )
    mood = (
        "Ultra eye-catching YouTube thumbnail background for a Christian prayer video. "
        f"{mood_line}. "
        f"{layout}"
        "Strong focal point, professional clickbait thumbnail style. "
        "NO text, NO letters, NO logos, NO watermark, NO readable words, NO faces close-up. "
        f"Visual mood for topic: {trend}. "
        f"Prayer context: {brief}. "
        f"Emotional hook (do NOT render as text): {thumbnail_title}. "
        f"Scene mood keywords: {theme}"
    )
    try:
        kwargs = {"model": model, "prompt": mood, "n": 1, "size": size}
        if model.startswith("dall-e-2"):
            kwargs["response_format"] = "b64_json"
        resp = await client.images.generate(**kwargs)
        if await _save_image_response_item(resp.data[0], dest):
            try:
                from bot.services.llm_usage_tracker import log_image_generation

                await log_image_generation(
                    model=model,
                    request_kind="cover_image",
                    usage=getattr(resp, "usage", None),
                    metadata={"size": size, "vertical": bool(vertical)},
                )
            except Exception:
                pass
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
                )
                if await _save_image_response_item(resp.data[0], dest):
                    try:
                        from bot.services.llm_usage_tracker import log_image_generation

                        await log_image_generation(
                            model="dall-e-3",
                            request_kind="cover_image",
                            usage=getattr(resp, "usage", None),
                        )
                    except Exception:
                        pass
                    return True
            except Exception as e2:
                logger.warning("cover dall-e-3 failed: %s", e2)
    return False


def _wrap_title(title: str, *, max_chars: int, max_lines: int = 3) -> str:
    """Перенос по словам. При переполнении ставим «…», а не теряем хвост молча."""
    words = title.split()
    limit = max(1, int(max_lines))
    lines: list[str] = []
    cur: list[str] = []
    n = 0
    overflow = False
    for w in words:
        add = len(w) + (1 if cur else 0)
        if cur and n + add > max_chars:
            lines.append(" ".join(cur))
            if len(lines) >= limit:
                overflow = True
                cur = []
                break
            cur = [w]
            n = len(w)
        else:
            cur.append(w)
            n += add
    if cur and len(lines) < limit:
        lines.append(" ".join(cur))
    elif cur:
        overflow = True
    if overflow and lines:
        tail = lines[-1].rstrip(" .,;:—–-")
        if len(tail) > max_chars - 1:
            tail = tail[: max_chars - 1].rstrip(" .,;:—–-")
        lines[-1] = tail + "…"
    return "\n".join(lines[:limit])


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
        f"drawbox=x=0:y=ih*0.45:w=iw:h=ih*0.55:color=black@0.48:t=fill,"
        f"drawtext=fontfile='{font_esc}':textfile='{text_esc}':"
        f"fontsize={fontsize}:fontcolor=0xFFF6E8:borderw=3:bordercolor=black@0.55:"
        f"line_spacing=16:"
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


def _ass_escape(text: str) -> str:
    return (text or "").replace("\\", "/").replace("{", "(").replace("}", ")")


def _write_cover_ass(
    path: Path,
    *,
    title: str,
    badge: str,
    width: int,
    height: int,
    fontsize: int,
) -> None:
    """Вёрстка обложки через libass.

    drawtext не умеет ни выравнивать многострочник, ни красить отдельную строку,
    а реальную высоту блока по метрикам шрифта из Python не посчитать. ASS решает
    и то и другое, и заодно даёт ту же типографику, что в самом ролике.
    """
    from youtube_prayer.render import display_font, font_is_heavy

    font = display_font()
    bold = 0 if font_is_heavy(font) else -1
    title_y = int(height * _V_TITLE_TOP_FRAC)
    accent_y = max(0, title_y - _V_ACCENT_GAP)
    lines = [
        ln
        for ln in _wrap_title(
            title, max_chars=_V_TITLE_MAX_CHARS, max_lines=_V_TITLE_MAX_LINES
        ).split("\n")
        if ln.strip()
    ] or [title]

    # Длинное слово («благодарность», «восстановление») переносить некуда, и оно
    # уезжает за правый край. Тогда уменьшаем кегль ровно настолько, чтобы влезло.
    # 0.94 — запас: оценка ширины приблизительная, впритык к краю ставить нельзя.
    usable = (width - 2 * _V_TITLE_LEFT) * 0.94
    widest = max((len(ln) for ln in lines), default=1)
    est = widest * fontsize * _V_CHAR_W_RATIO
    if est > usable:
        fontsize = max(_V_TITLE_MIN_SIZE, int(fontsize * usable / est))

    # Последняя строка — золотом: классический приём «крючок + добивка».
    parts = [_ass_escape(ln) for ln in lines]
    if len(parts) >= 2:
        parts[-1] = f"{{\\c{_COL_GOLD}}}{parts[-1]}"
    head_text = "\\N".join(parts)

    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Head,{font},{fontsize},{_COL_HEAD},&H000000FF,&H30000000,&H50000000,{bold},0,0,0,100,100,-1.5,0,1,5.0,4.0,7,{_V_TITLE_LEFT},{_V_TITLE_LEFT},{title_y},1
Style: Badge,{font},{_V_BADGE_SIZE},{_COL_BADGE},&H000000FF,&H60000000,&H00000000,{bold},0,0,0,100,100,4.0,0,1,2.5,0,7,0,0,0,1
Style: Rule,{font},40,{_COL_GOLD},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = [head]
    ev.append(
        f"Dialogue: 1,0:00:00.00,0:00:10.00,Rule,,0,0,0,,"
        f"{{\\p1\\pos({_V_TITLE_LEFT},{accent_y})}}"
        f"m 0 0 l {_V_ACCENT_W} 0 l {_V_ACCENT_W} {_V_ACCENT_H} l 0 {_V_ACCENT_H}"
    )
    ev.append(f"Dialogue: 1,0:00:00.00,0:00:10.00,Head,,0,0,0,,{head_text}")
    if badge.strip():
        # Бейдж под блоком заголовка: строк известно сколько, а высота строки
        # у libass ≈ 1.2 кегля — этого хватает, чтобы не наехать на текст.
        badge_y = title_y + int(len(lines) * fontsize * 1.2) + _V_BADGE_GAP
        ev.append(
            f"Dialogue: 1,0:00:00.00,0:00:10.00,Badge,,0,0,0,,"
            f"{{\\pos({_V_TITLE_LEFT},{badge_y})}}{_ass_escape(badge.upper())}"
        )
    path.write_text("\n".join(ev) + "\n", encoding="utf-8")


def _burn_title_vertical(
    bg: Path,
    dest: Path,
    *,
    title: str,
    badge: str = "молитва · 1 минута",
    width: int = _SHORT_W,
    height: int = _SHORT_H,
) -> bool:
    """Обложка 9:16: огромный заголовок сверху, золотой акцент, градиент."""
    from youtube_prayer.render import build_scrim_png

    ffmpeg = _ffmpeg()
    fontsize = int(os.getenv("YT_PRAYER_COVER_TITLE_SIZE") or _V_TITLE_SIZE)
    ass_path = dest.with_suffix(".ass")
    _write_cover_ass(
        ass_path,
        title=title,
        badge=badge,
        width=width,
        height=height,
        fontsize=fontsize,
    )
    ass_esc = ass_path.resolve().as_posix().replace("\\", "/").replace(":", "\\:")
    draw = f"ass='{ass_esc}'"
    fit = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}"
    )

    scrim = dest.with_name(f"{dest.stem}_scrim.png")
    has_scrim = build_scrim_png(
        scrim,
        width=width,
        height=height,
        top_frac=0.68,
        bottom_frac=0.16,
        top_alpha=0.82,
        bottom_alpha=0.45,
        top_hold=0.76,
    )

    def _run(with_scrim: bool) -> bool:
        if with_scrim:
            cmd = [
                ffmpeg, "-y", "-i", str(bg), "-i", str(scrim),
                "-filter_complex",
                f"[0:v]{fit}[bgv];[1:v]scale={width}:{height}[sc];"
                f"[bgv][sc]overlay=0:0:format=auto[lay];[lay]{draw}[v]",
                "-map", "[v]", "-frames:v", "1", "-q:v", "2", str(dest),
            ]
        else:
            cmd = [
                ffmpeg, "-y", "-i", str(bg),
                "-vf",
                f"{fit},drawbox=x=0:y=0:w=iw:h=ih*0.60:color=black@0.55:t=fill,{draw}",
                "-frames:v", "1", "-q:v", "2", str(dest),
            ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, check=False
        )
        if proc.returncode != 0 or not dest.is_file():
            logger.warning(
                "vertical burn (scrim=%s) failed: %s",
                with_scrim,
                (proc.stderr or "")[-400:],
            )
            return False
        return True

    if has_scrim and _run(True):
        return True
    return _run(False)


def _extract_broll_frame(
    broll: Path,
    dest: Path,
    *,
    width: int,
    height: int,
    t_sec: float = 3.0,
) -> bool:
    if not broll.is_file():
        return False
    ffmpeg = _ffmpeg()
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}"
    )
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        str(t_sec),
        "-i",
        str(broll),
        "-vf",
        vf,
        "-frames:v",
        "1",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
    if proc.returncode != 0 or not dest.is_file():
        logger.warning("broll frame extract failed: %s", (proc.stderr or "")[-300:])
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
    broll_path: Optional[Path] = None,
    hook_question: str = "",
    n_variants: Optional[int] = None,
) -> Optional[CoverPack]:
    """AI-обложка 16:9: 3–4 варианта на выбор + primary для YouTube."""
    work_dir.mkdir(parents=True, exist_ok=True)
    video_title = (title or "").strip()
    thumb_title = (thumbnail_title or video_title).strip()
    if len(thumb_title) < 3:
        logger.error("cover: empty thumbnail title")
        return None
    (work_dir / "cover_title.txt").write_text(thumb_title + "\n", encoding="utf-8")

    try:
        n = int(
            n_variants
            if n_variants is not None
            else (os.getenv("YT_PRAYER_COVER_VARIANTS") or "4")
        )
    except ValueError:
        n = 4
    n = max(1, min(4, n))

    variants: List[Path] = []
    for vi in range(n):
        mood = _COVER_VARIANT_MOODS[vi % len(_COVER_VARIANT_MOODS)]
        bg_h = work_dir / f"cover_bg_h_{vi + 1}.png"
        ok_h = False
        for attempt in range(1, _GEN_ATTEMPTS + 1):
            ok_h = await _gen_cover_bg(
                trend=trend,
                brief=brief,
                thumbnail_title=thumb_title,
                broll_query=broll_query,
                dest=bg_h,
                mood_extra=mood,
            )
            if ok_h:
                break
            logger.warning(
                "cover AI variant %s attempt %s/%s failed",
                vi + 1,
                attempt,
                _GEN_ATTEMPTS,
            )
            await asyncio.sleep(0.6 * attempt)
        if not ok_h and vi == 0 and broll_path:
            if _extract_broll_frame(
                broll_path, bg_h, width=_FULL_W, height=_FULL_H
            ):
                logger.info("cover fallback: frame from b-roll")
                ok_h = True
        if not ok_h:
            if vi == 0:
                logger.error(
                    "cover AI generation failed after %s attempts", _GEN_ATTEMPTS
                )
                return None
            continue

        out_h = work_dir / f"cover_16x9_v{vi + 1}.jpg"
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
        if ok and out_h.is_file():
            variants.append(out_h)

    if not variants:
        return None

    # primary = первый удачный (для YouTube upload)
    primary = work_dir / "cover_16x9.jpg"
    try:
        import shutil

        shutil.copy2(variants[0], primary)
    except Exception:
        primary = variants[0]

    hook = (hook_question or "").strip()
    if hook:
        (work_dir / "hook_question.txt").write_text(hook + "\n", encoding="utf-8")

    logger.info(
        "cover ok variants=%s thumb=%r yt_title=%r hook=%r",
        len(variants),
        thumb_title,
        video_title,
        (hook[:60] if hook else ""),
    )
    return CoverPack(
        title=video_title,
        thumbnail_title=thumb_title,
        horizontal=primary,
        variants=tuple(variants),
        hook_question=hook,
    )


async def generate_vertical_cover_pack(
    work_dir: Path,
    *,
    title: str,
    thumbnail_title: str,
    trend: str,
    brief: str = "",
    broll_query: str = "",
    broll_path: Optional[Path] = None,
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
            vertical=True,
        )
        if ok_v:
            break
        logger.warning("vertical cover AI attempt %s/%s failed", attempt, _GEN_ATTEMPTS)
        await asyncio.sleep(0.8 * attempt)
    if not ok_v:
        if broll_path and _extract_broll_frame(
            broll_path, bg_v, width=_SHORT_W, height=_SHORT_H
        ):
            logger.info("vertical cover fallback: frame from b-roll")
            ok_v = True
    if not ok_v:
        logger.error("vertical cover AI failed after %s attempts", _GEN_ATTEMPTS)
        return None

    out_v = work_dir / "cover_9x16.jpg"
    ok = await asyncio.to_thread(
        _burn_title_vertical,
        bg_v,
        out_v,
        title=thumb_title,
        width=_SHORT_W,
        height=_SHORT_H,
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
        variants=(out_v,),
    )
