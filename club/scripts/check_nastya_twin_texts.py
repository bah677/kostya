#!/usr/bin/env python3
"""Проверка: все файлы из bot/texts есть в twin_texts/nastya (overlay с --delete)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN_TEXTS = ROOT / "bot" / "texts"
TWIN_TEXTS = ROOT / "twin_texts" / "nastya"


def _iter_text_files(base: Path) -> list[Path]:
    out: list[Path] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts:
            continue
        out.append(path.relative_to(base))
    return out


def main() -> int:
    missing = []
    for rel in _iter_text_files(MAIN_TEXTS):
        twin = TWIN_TEXTS / rel
        if not twin.is_file():
            missing.append(str(rel))
    if missing:
        print(
            "ERROR: для Nastya не хватает twin-текстов (deploy удалит их из bot/texts):",
            file=sys.stderr,
        )
        for name in missing:
            print(f"  - twin_texts/nastya/{name}", file=sys.stderr)
        print(
            f"\nПодсказка: скопируйте из bot/texts, например:\n"
            f"  cp bot/texts/<path> twin_texts/nastya/<path>",
            file=sys.stderr,
        )
        return 1
    n = len(_iter_text_files(MAIN_TEXTS))
    print(f"OK: {n} файлов bot/texts покрыты twin_texts/nastya")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
