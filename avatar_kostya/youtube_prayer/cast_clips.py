"""Оживление кадров банка: из картинки — десять секунд движения.

Картинка висит в ролике восемь-пятнадцать секунд, и неподвижный кадр с
медленным наездом всё равно читается как слайд-шоу. Здесь из того же кадра
делается короткий клип: дыхание, моргание, пар над кружкой, свет.

Почему десять секунд, а не пять с петлёй: шортсы у нас по сто девять секунд
в среднем, тринадцати пятисекундных клипов на них не хватает. Растягивать
петлёй нельзя — разворот времени гонит пар обратно в кружку, а наплыв даёт
тридцать растворений за ролик. Десять секунд закрывают почти всё без петель.

Кадр из банка идёт первым кадром клипа, поэтому героиня остаётся собой:
модель не придумывает лицо заново, а оживляет готовое.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from youtube_prayer.cast import Scene, scene_by_key, scenes_for
from youtube_prayer.cast_bank import _owner, frames_for_scene

logger = logging.getLogger(__name__)

# Standard-ветка Kling: $0.42 за десять секунд. Pro вчетверо дороже и на
# нашем движении — дыхание и пар — разницы не даёт.
MODEL = "fal-ai/kling-video/v2.5-turbo/standard/image-to-video"
SECONDS = "10"
PRICE_PER_CLIP = 0.42

# Опору ужимаем: модель всё равно отдаёт около 780 по ширине, а гнать
# двухмегабайтный png в теле запроса незачем.
_REF_SIZE = (768, 1152)

_CAMERA_RULE = (
    "Static locked-off camera, absolutely no camera movement, no zoom, "
    "no pan, no tilt, no dolly."
)
_KEEP_RULE = (
    "Nothing else changes: the pose, the framing, the clothing, the faces "
    "and the light stay exactly as they are. No cuts, no new objects, "
    "no text, no captions."
)
NEGATIVE = (
    "camera movement, zoom, pan, tilt, cut, scene change, text, caption, "
    "watermark, logo, distorted face, changing face, different person, "
    "morphing, extra fingers, hair across the face, fast motion, "
    "exaggerated gestures"
)


def api_key() -> str:
    key = (os.getenv("FAL_KEY") or "").strip()
    if not key:
        try:
            from config import config

            key = str(getattr(config, "FAL_KEY", "") or "").strip()
        except Exception:
            key = ""
    if not key:
        raise RuntimeError("FAL_KEY не задан")
    return key


def clip_path(scene: Scene, variant: int = 1, heroine: str = "") -> Path:
    from youtube_prayer.cast_bank import image_path

    return image_path(scene, variant, heroine).with_suffix(".mp4")


def clips_for_scene(scene: Scene, heroine: str = "") -> List[Path]:
    """Готовые клипы сцены. Клип лежит рядом со своим кадром."""
    out = []
    for frame in frames_for_scene(scene, heroine):
        mp4 = frame.with_suffix(".mp4")
        if mp4.is_file() and mp4.stat().st_size > 50_000:
            out.append(mp4)
    return out


def motion_prompt(scene: Scene) -> str:
    """Что именно движется. Без этого модель двигает камеру и героиню."""
    motion = (scene.motion or "").strip() or (
        "Only breathing and the light move; everyone stays still."
    )
    return f"{_CAMERA_RULE} {motion} {_KEEP_RULE}"


def balance() -> float:
    import httpx

    r = httpx.get(
        "https://rest.alpha.fal.ai/billing/user_balance",
        headers={"Authorization": f"Key {api_key()}"},
        timeout=30,
    )
    r.raise_for_status()
    return float(r.text.strip() or 0)


def _reference(frame: Path) -> str:
    from PIL import Image

    buf = io.BytesIO()
    with Image.open(frame) as im:
        im.convert("RGB").resize(_REF_SIZE, Image.LANCZOS).save(buf, "JPEG", quality=92)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def animate(frame: Path, scene: Scene, *, timeout_sec: float = 900.0) -> Path:
    """Оживляет один кадр. Возвращает путь к готовому клипу."""
    import httpx

    dest = frame.with_suffix(".mp4")
    if dest.is_file() and dest.stat().st_size > 50_000:
        return dest

    h = {"Authorization": f"Key {api_key()}"}
    body = {
        "prompt": motion_prompt(scene),
        "negative_prompt": NEGATIVE,
        "image_url": _reference(frame),
        "duration": SECONDS,
    }
    r = httpx.post(f"https://queue.fal.run/{MODEL}", headers=h, json=body, timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
    job = r.json()
    rid = job["request_id"]
    status_url = job.get("status_url") or f"https://queue.fal.run/{MODEL}/requests/{rid}/status"
    resp_url = job.get("response_url") or f"https://queue.fal.run/{MODEL}/requests/{rid}"

    t0 = time.time()
    status = ""
    while True:
        status = httpx.get(status_url, headers=h, timeout=60).json().get("status", "")
        if status in ("COMPLETED", "FAILED", "ERROR"):
            break
        if time.time() - t0 > timeout_sec:
            raise TimeoutError(f"клип не собрался за {int(timeout_sec)} с")
        time.sleep(10)
    if status != "COMPLETED":
        raise RuntimeError(f"статус {status}")

    out = httpx.get(resp_url, headers=h, timeout=120).json()
    url = (out.get("video") or {}).get("url")
    if not url:
        raise RuntimeError("в ответе нет видео")
    tmp = dest.with_suffix(".part")
    with httpx.stream("GET", url, timeout=600) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)
    tmp.replace(dest)
    return dest


def pending(heroine: str = "", keys: Optional[List[str]] = None) -> List[Tuple[Scene, Path]]:
    """Кадры, которые ещё не оживлены."""
    wanted = {k.strip() for k in (keys or []) if k.strip()}
    out: List[Tuple[Scene, Path]] = []
    for scene in scenes_for(heroine):
        if wanted and scene.key not in wanted:
            continue
        for frame in frames_for_scene(scene, heroine):
            mp4 = frame.with_suffix(".mp4")
            if not (mp4.is_file() and mp4.stat().st_size > 50_000):
                out.append((scene, frame))
    return out


def animate_missing(
    heroine: str = "",
    keys: Optional[List[str]] = None,
    *,
    limit: int = 0,
    on_step=None,
) -> Dict[str, Any]:
    """Оживляет всё недостающее. Готовое не трогает — запуск повторяем смело."""
    todo = pending(heroine, keys)
    if limit > 0:
        todo = todo[:limit]
    made, failed, errors = 0, 0, []
    for scene, frame in todo:
        try:
            dest = animate(frame, scene)
            made += 1
            if on_step:
                on_step(scene, dest, None)
        except Exception as e:
            failed += 1
            errors.append(f"{scene.key}: {type(e).__name__}: {str(e)[:140]}")
            if on_step:
                on_step(scene, frame, e)
    return {"made": made, "failed": failed, "errors": errors, "total": len(todo)}
