#!/usr/bin/env python3
"""Влияют ли герои на просмотры — с поправкой на час публикации.

Сравнивать «с героями» и «сток» в лоб нельзя. Час премьеры выбирается по
номеру ролика в пачке, тема у номера своя и неизменная, и раньше герои
всегда доставались первым номерам. Три признака двигались вместе, а
утренний слот даёт вчетверо больше ночного — перекос решал исход заранее.

Поэтому сравниваем только внутри одного слота и на одном возрасте:
просмотры накапливаются, вчерашний ролик с сегодняшним несравним.

    .venv/bin/python3 scripts/cast_effect_report.py
    .venv/bin/python3 scripts/cast_effect_report.py --age 2 --since 2026-10-20
"""

from __future__ import annotations

import argparse
import json
import re
import statistics as st
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

SHORTS = _ROOT / "data/youtube_shorts"
STATS = _ROOT / "data/youtube_stats"


def collect() -> List[Dict[str, Any]]:
    """Ролики со слотом, темой, признаком героев и просмотрами по возрасту."""
    meta: Dict[str, Dict[str, Any]] = {}
    for day_dir in sorted(SHORTS.glob("20*-*-*")):
        for item in sorted(p for p in day_dir.iterdir() if p.is_dir()):
            up = item / "youtube_upload.json"
            if not up.is_file():
                continue
            try:
                d = json.loads(up.read_text(encoding="utf-8"))
            except Exception:
                continue
            vid = str(d.get("video_id") or "")
            if not vid:
                continue
            m = re.match(r"(\d+)_(.+)", item.name)
            meta[vid] = {
                "day": day_dir.name,
                "index": int(m.group(1)) if m else 0,
                "topic": m.group(2) if m else item.name,
                "slot": str(d.get("publish_at_msk") or "")[11:16] or "—",
                # Следы монтажа с героями остаются в рабочей папке ролика.
                "cast": any(item.glob("cast_*")),
            }

    series: Dict[str, Dict[str, int]] = {}
    for p in sorted(STATS.glob("20*-*-*.json")):
        if "_" in p.stem:      # испанские срезы лежат с суффиксом
            continue
        try:
            snap = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for v in snap.get("videos", []):
            series.setdefault(str(v.get("id")), {})[p.stem] = int(v.get("views") or 0)

    rows = []
    for vid, m in meta.items():
        if vid not in series:
            continue
        pub = datetime.strptime(m["day"], "%Y-%m-%d").date()
        by_age = {}
        for day, views in series[vid].items():
            age = (datetime.strptime(day, "%Y-%m-%d").date() - pub).days
            if age >= 0:
                by_age[age] = views
        if by_age:
            rows.append({**m, "id": vid, "by_age": by_age})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Эффект героев с поправкой на слот")
    ap.add_argument("--age", type=int, default=1, help="на какие сутки смотреть")
    ap.add_argument("--since", default="", help="не раньше этой даты")
    args = ap.parse_args()

    rows = [r for r in collect() if not args.since or r["day"] >= args.since]
    rows = [r for r in rows if args.age in r["by_age"]]
    if not rows:
        print("Нет данных на такой возраст — срезов ещё мало.")
        return 1

    def views(r):
        return r["by_age"][args.age]

    print(f"Просмотры на {args.age}-е сутки, роликов {len(rows)}\n")
    print(f"{'слот':7s} {'с героями':>22s} {'сток':>22s}   разница")
    deltas = []
    for slot in sorted({r["slot"] for r in rows}):
        hero = [views(r) for r in rows if r["slot"] == slot and r["cast"]]
        stock = [views(r) for r in rows if r["slot"] == slot and not r["cast"]]
        h = f"{st.median(hero):6.0f} (n={len(hero)})" if hero else "—"
        s = f"{st.median(stock):6.0f} (n={len(stock)})" if stock else "—"
        diff = ""
        if hero and stock:
            d = (st.median(hero) / max(1, st.median(stock)) - 1) * 100
            deltas.append(d)
            diff = f"{d:+5.0f}%"
        print(f"{slot:7s} {h:>22s} {s:>22s}   {diff}")

    if deltas:
        print(f"\nсредняя разница по слотам: {st.mean(deltas):+.0f}%")
        if len(deltas) < 3 or min(len(deltas), 3) < 3:
            print("Слотов со сравнением мало — числа пока ни о чём не говорят.")
    else:
        print("\nНи в одном слоте нет обеих групп — сравнивать не с чем.")
        print("Герои должны попадать в разные слоты: см. cast_indices в pipeline.")

    pairs = sum(1 for r in rows if r["cast"])
    print(f"\nроликов с героями в выборке: {pairs}")
    if pairs < 18:
        print("Для разницы в треть нужно около 18 на группу — это примерно шесть дней.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
