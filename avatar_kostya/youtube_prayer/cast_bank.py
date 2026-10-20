"""Банк кадров с героями: где лежит, чего не хватает, как дорисовать.

Раньше это жило в scripts/build_cast_bank.py, а cast_broll держал свою
копию bank_dir — две функции с одинаковым телом в разных файлах. Теперь
одно место: и ночной конвейер, и консольный скрипт, и студия берут кадры
отсюда.

Генерим только недостающее: повторный запуск ничего не перерисовывает и не
тратит деньги заново. Банк переиспользуется всеми роликами — тем у канала
конечное число.
"""

from __future__ import annotations

import base64
import logging
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

from youtube_prayer.cast import Scene, full_prompt, scene_by_key, scenes

logger = logging.getLogger(__name__)

# Вертикаль под Shorts. gpt-image-1 отдаёт 1024x1536 — этого хватает: кадр
# всё равно уходит в Ken Burns с масштабом, а не ставится пиксель в пиксель.
SIZE = "1024x1536"

# Файл меньше килобайта — это обрывок закачки, а не кадр.
_MIN_BYTES = 1000
_NAME_RE = re.compile(r"^[a-z0-9_\-]+_(\d{2})\.png$")


def bank_dir() -> Path:
    raw = (os.getenv("YT_PRAYER_CAST_BANK_DIR") or "").strip()
    p = Path(raw) if raw else Path("data/cast_bank")
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[1] / p
    return p


def image_path(scene: Scene, variant: int) -> Path:
    return bank_dir() / scene.who / f"{scene.key}_{variant:02d}.png"


def frames_for_scene(scene: Scene) -> List[Path]:
    """Все варианты кадра: в банке их может быть по нескольку на сцену."""
    d = bank_dir() / scene.who
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob(f"{scene.key}_*.png") if p.stat().st_size > _MIN_BYTES)


def next_variant(scene: Scene) -> int:
    """Номер следующего дубля: первый свободный, а не следующий за последним."""
    taken = set()
    for p in frames_for_scene(scene):
        m = _NAME_RE.match(p.name)
        if m:
            taken.add(int(m.group(1)))
    n = 1
    while n in taken:
        n += 1
    return n


def frame_file(who: str, name: str) -> Optional[Path]:
    """Путь к кадру по имени из запроса. Чужие пути не отдаём."""
    from youtube_prayer.cast_store import WHO_VALUES

    if who not in WHO_VALUES or not _NAME_RE.match(name or ""):
        return None
    p = bank_dir() / who / name
    if not p.is_file() or p.stat().st_size <= _MIN_BYTES:
        return None
    return p


def thumb_file(path: Path, *, width: int = 300) -> Optional[Path]:
    """Маленький JPEG рядом с кадром — для галереи в студии.

    Сам кадр весит около двух с половиной мегабайт, и тринадцать таких на
    одной странице — тридцать мегабайт на каждое открытие. Превью делается
    один раз и живёт рядом в .thumbs.
    """
    dest = path.parent / ".thumbs" / f"{path.stem}.jpg"
    try:
        if dest.is_file() and dest.stat().st_mtime >= path.stat().st_mtime:
            return dest
        from PIL import Image

        dest.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(path) as im:
            im = im.convert("RGB")
            h = max(1, round(im.height * width / im.width))
            im.resize((width, h), Image.LANCZOS).save(dest, "JPEG", quality=82)
        return dest
    except Exception as e:
        logger.warning("превью %s не сделано: %s", path.name, e)
        return None


def model_name(model: str = "") -> str:
    return (model or os.getenv("YT_PRAYER_IMAGE_MODEL") or "gpt-image-1").strip()


def price_per_frame(model: str = "") -> float:
    """Оценка из того же прайса, по которому считается отчёт о расходах."""
    try:
        from bot.services.llm_cost_report import _IMAGE_PER_CALL

        return float(_IMAGE_PER_CALL.get(model_name(model), 0.04))
    except Exception:
        return 0.04


async def generate_one(client, scene: Scene, variant: int, model: str) -> bool:
    """Рисует один кадр. False — если он уже есть и перерисовывать нечего."""
    dest = image_path(scene, variant)
    if dest.is_file() and dest.stat().st_size > _MIN_BYTES:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = await client.images.generate(
        model=model, prompt=full_prompt(scene), n=1, size=SIZE
    )
    item = resp.data[0]
    raw = getattr(item, "b64_json", None)
    if raw:
        dest.write_bytes(base64.b64decode(raw))
    else:
        url = getattr(item, "url", None)
        if not url:
            raise RuntimeError("модель не вернула изображение")
        import httpx

        async with httpx.AsyncClient(timeout=120.0) as http:
            r = await http.get(url)
            r.raise_for_status()
            dest.write_bytes(r.content)
    try:
        from bot.services.llm_usage_tracker import log_image_generation

        await log_image_generation(
            model=model, request_kind="cast_bank", usage=getattr(resp, "usage", None)
        )
    except Exception:
        logger.debug("учёт расхода не записан", exc_info=True)
    return True


def make_client(model: str = ""):
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        try:
            from config import config

            key = str(getattr(config, "OPENAI_API_KEY", "") or "").strip()
        except Exception:
            key = ""
    if not key:
        raise RuntimeError("OPENAI_API_KEY не задан")
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=key, timeout=180.0)


async def generate_scene(key: str, *, model: str = "") -> Tuple[str, str]:
    """Дорисовать один дубль указанного кадра. (имя файла, что сделали)."""
    scene = scene_by_key(key)
    if scene is None:
        raise ValueError(f"нет кадра с ключом «{key}»")
    mdl = model_name(model)
    variant = next_variant(scene)
    client = make_client(mdl)
    made = await generate_one(client, scene, variant, mdl)
    name = image_path(scene, variant).name
    return name, ("нарисован" if made else "уже был")


async def generate_missing(
    keys: Optional[List[str]] = None, *, variants: int = 1, model: str = ""
) -> dict:
    """Добить банк до нужного числа дублей. Готовое не трогает."""
    wanted = {k.strip() for k in (keys or []) if k.strip()}
    chosen = [s for s in scenes() if not wanted or s.key in wanted]
    mdl = model_name(model)
    made = skipped = failed = 0
    errors: List[str] = []
    client = None
    for s in chosen:
        for v in range(1, max(1, variants) + 1):
            if image_path(s, v).is_file():
                skipped += 1
                continue
            if client is None:
                client = make_client(mdl)
            try:
                if await generate_one(client, s, v, mdl):
                    made += 1
                else:
                    skipped += 1
            except Exception as e:
                failed += 1
                errors.append(f"{s.key}: {type(e).__name__}: {str(e)[:120]}")
    return {"made": made, "skipped": skipped, "failed": failed, "errors": errors}
