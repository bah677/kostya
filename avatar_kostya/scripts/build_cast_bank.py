#!/usr/bin/env python3
"""Собирает банк кадров с постоянными героями.

Генерит только недостающее: запуск повторно ничего не перерисовывает и не
тратит деньги заново. Банк живёт в data/cast_bank и переиспользуется всеми
роликами — темы у канала конечные, рисовать каждый раз заново незачем.

    .venv/bin/python3 scripts/build_cast_bank.py            # всё недостающее
    .venv/bin/python3 scripts/build_cast_bank.py --only hand_shoulder,kitchen_night
    .venv/bin/python3 scripts/build_cast_bank.py --variants 2   # по 2 дубля на кадр
    .venv/bin/python3 scripts/build_cast_bank.py --dry-run      # только показать промпты
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from youtube_prayer.cast import SCENES, Scene, full_prompt  # noqa: E402

logger = logging.getLogger("cast_bank")

# Вертикаль под Shorts. gpt-image-1 отдаёт 1024x1536 — этого хватает: кадр
# всё равно уходит в Ken Burns с масштабом, а не ставится пиксель в пиксель.
SIZE = "1024x1536"


def bank_dir() -> Path:
    raw = (os.getenv("YT_PRAYER_CAST_BANK_DIR") or "").strip()
    p = Path(raw) if raw else Path("data/cast_bank")
    if not p.is_absolute():
        p = _ROOT / p
    return p


def image_path(scene: Scene, variant: int) -> Path:
    return bank_dir() / scene.who / f"{scene.key}_{variant:02d}.png"


async def generate_one(client, scene: Scene, variant: int, model: str) -> bool:
    dest = image_path(scene, variant)
    if dest.is_file() and dest.stat().st_size > 1000:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    prompt = full_prompt(scene)
    resp = await client.images.generate(
        model=model, prompt=prompt, n=1, size=SIZE
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


async def main() -> int:
    ap = argparse.ArgumentParser(description="Банк кадров с постоянными героями")
    ap.add_argument("--only", default="", help="ключи кадров через запятую")
    ap.add_argument("--variants", type=int, default=1, help="дублей на кадр")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default="")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    wanted = {k.strip() for k in args.only.split(",") if k.strip()}
    scenes = [s for s in SCENES if not wanted or s.key in wanted]
    if not scenes:
        print("Ничего не выбрано. Доступные ключи:", file=sys.stderr)
        print(", ".join(s.key for s in SCENES), file=sys.stderr)
        return 1

    if args.dry_run:
        for s in scenes:
            print(f"\n=== {s.who}/{s.key}")
            print(full_prompt(s))
        return 0

    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        print("OPENAI_API_KEY не задан", file=sys.stderr)
        return 1
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=key, timeout=180.0)
    model = (args.model or os.getenv("YT_PRAYER_IMAGE_MODEL") or "gpt-image-1").strip()

    made = skipped = failed = 0
    for s in scenes:
        for v in range(1, max(1, args.variants) + 1):
            dest = image_path(s, v)
            try:
                if await generate_one(client, s, v, model):
                    made += 1
                    print(f"  + {s.who}/{dest.name}")
                else:
                    skipped += 1
                    print(f"  = {s.who}/{dest.name} (уже есть)")
            except Exception as e:
                failed += 1
                print(f"  ! {s.who}/{dest.name}: {e}", file=sys.stderr)

    print(f"\nготово: новых {made}, пропущено {skipped}, ошибок {failed}")
    print(f"банк: {bank_dir()}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
