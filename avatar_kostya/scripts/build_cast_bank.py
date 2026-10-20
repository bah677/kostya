#!/usr/bin/env python3
"""Собирает банк кадров с постоянными героями.

Генерит только недостающее: запуск повторно ничего не перерисовывает и не
тратит деньги заново. Банк живёт в data/cast_bank и переиспользуется всеми
роликами — темы у канала конечные, рисовать каждый раз заново незачем.

    .venv/bin/python3 scripts/build_cast_bank.py            # всё недостающее
    .venv/bin/python3 scripts/build_cast_bank.py --only hand_shoulder,kitchen_night
    .venv/bin/python3 scripts/build_cast_bank.py --heroine bright  # вторая героиня
    .venv/bin/python3 scripts/build_cast_bank.py --variants 2   # по 2 дубля на кадр
    .venv/bin/python3 scripts/build_cast_bank.py --dry-run      # только показать промпты
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from youtube_prayer.cast import (  # noqa: E402
    bank_folder,
    full_prompt,
    heroine,
    scenes_for,
)
from youtube_prayer.cast_bank import (  # noqa: E402
    bank_dir,
    generate_one,
    image_path,
    make_client,
    model_name,
)

logger = logging.getLogger("cast_bank")

async def main() -> int:
    ap = argparse.ArgumentParser(description="Банк кадров с постоянными героями")
    ap.add_argument("--only", default="", help="ключи кадров через запятую")
    ap.add_argument("--heroine", default="", help="ключ героини: real | bright")
    ap.add_argument("--variants", type=int, default=1, help="дублей на кадр")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default="")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    who = (args.heroine or "").strip()
    wanted = {k.strip() for k in args.only.split(",") if k.strip()}
    # Только сцены этой героини плюс общие. Брать весь список нельзя:
    # у героинь разные наборы, и чужие сцены лягут в её папку и спишут
    # деньги за кадры, которые никогда не покажут.
    available = scenes_for(who)
    chosen = [s for s in available if not wanted or s.key in wanted]
    if not chosen:
        print("Ничего не выбрано. Доступные ключи:", file=sys.stderr)
        print(", ".join(s.key for s in available), file=sys.stderr)
        return 1

    if args.dry_run:
        for s in chosen:
            print(f"\n=== {bank_folder(s.who, who)}/{s.key}")
            print(full_prompt(s, who))
        return 0

    model = model_name(args.model)
    try:
        client = make_client(model)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1

    made = skipped = failed = 0
    for s in chosen:
        for v in range(1, max(1, args.variants) + 1):
            dest = image_path(s, v, who)
            try:
                if await generate_one(client, s, v, model, who):
                    made += 1
                    print(f"  + {s.who}/{dest.name}")
                else:
                    skipped += 1
                    print(f"  = {s.who}/{dest.name} (уже есть)")
            except Exception as e:
                failed += 1
                print(f"  ! {s.who}/{dest.name}: {e}", file=sys.stderr)

    print(f"\nготово: новых {made}, пропущено {skipped}, ошибок {failed}")
    print(f"героиня: {heroine(who).label}")
    print(f"банк: {bank_dir()}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
