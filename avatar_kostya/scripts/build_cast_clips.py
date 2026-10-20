#!/usr/bin/env python3
"""Оживляет кадры банка: из картинки — десять секунд движения.

Генерит только недостающее: повторный запуск ничего не переделывает и не
тратит деньги заново. Перед стартом проверяет баланс — на середине пачки
кончившиеся деньги дают наполовину оживший банк.

    .venv/bin/python3 scripts/build_cast_clips.py --heroine real
    .venv/bin/python3 scripts/build_cast_clips.py --heroine bright --limit 2
    .venv/bin/python3 scripts/build_cast_clips.py --only jesus_hands
    .venv/bin/python3 scripts/build_cast_clips.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from youtube_prayer.cast import heroine  # noqa: E402
from youtube_prayer.cast_clips import (  # noqa: E402
    PRICE_PER_CLIP,
    SECONDS,
    animate,
    balance,
    motion_prompt,
    pending,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Оживление кадров банка")
    ap.add_argument("--heroine", default="", help="ключ героини: real | bright")
    ap.add_argument("--only", default="", help="ключи сцен через запятую")
    ap.add_argument("--limit", type=int, default=0, help="сколько клипов за раз")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    who = (args.heroine or "").strip()
    keys = [k.strip() for k in args.only.split(",") if k.strip()]
    todo = pending(who, keys)
    if args.limit > 0:
        todo = todo[: args.limit]

    if not todo:
        print("Всё уже оживлено.")
        return 0

    cost = len(todo) * PRICE_PER_CLIP
    print(f"героиня: {heroine(who).label}")
    print(f"оживить: {len(todo)} кадров по {SECONDS} с — примерно ${cost:.2f}")

    if args.dry_run:
        for scene, frame in todo:
            print(f"\n=== {frame.parent.name}/{frame.stem}")
            print(motion_prompt(scene))
        return 0

    try:
        have = balance()
    except Exception as e:
        print(f"баланс не проверить ({e}) — продолжаю", file=sys.stderr)
        have = cost
    else:
        print(f"на счету: ${have:.2f}")
        if have < cost:
            print(
                f"Не хватает ${cost - have:.2f}. Пополните счёт или возьмите "
                f"часть через --limit {int(have // PRICE_PER_CLIP)}.",
                file=sys.stderr,
            )
            return 1

    made = failed = 0
    for i, (scene, frame) in enumerate(todo, 1):
        tag = f"{frame.parent.name}/{frame.stem}"
        try:
            dest = animate(frame, scene)
            made += 1
            mb = dest.stat().st_size / 1024 / 1024
            print(f"  [{i}/{len(todo)}] + {tag}  {mb:.1f} МБ")
        except Exception as e:
            failed += 1
            print(f"  [{i}/{len(todo)}] ! {tag}: {e}", file=sys.stderr)

    print(f"\nготово: оживлено {made}, ошибок {failed}")
    try:
        print(f"остаток: ${balance():.2f}")
    except Exception:
        pass
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
