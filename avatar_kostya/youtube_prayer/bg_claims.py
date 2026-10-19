"""Отрезки фоновой музыки, на которые заявился Content ID.

Фон берётся со случайной секунды часового сборника. Часть композиций в этих
сборниках заявлена правообладателями с политикой «блокировать»: ролик,
случайно попавший на такой отрезок, YouTube закрывает во всех странах.
Отсюда и редкость — четыре ролика из 95, а не треть.

Здесь лежит список уже известных отрезков. Микшер их обходит, а ночная сверка
пополняет список сама: нашла заблокированный ролик — взяла из его
audio_mix.json трек и секунды, записала сюда. Один бан на отрезок, дальше
он не повторяется.
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Запас вокруг заявленного окна: точных границ композиции мы не знаем, знаем
# только окно, которое взял заблокированный ролик.
MARGIN_SEC = 20.0

_FILE_NAME = "bg_claims.json"


def claims_path() -> Path:
    import os

    raw = (os.getenv("YT_PRAYER_BG_CLAIMS_FILE") or "").strip()
    if raw:
        p = Path(raw)
    else:
        p = Path("data/youtube_prayer") / _FILE_NAME
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[1] / p
    return p


def load_claims() -> Dict[str, List[List[float]]]:
    p = claims_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("bg_claims: файл не прочитан (%s) — считаем пустым", e)
        return {}
    out: Dict[str, List[List[float]]] = {}
    for track, spans in (data or {}).items():
        clean = []
        for s in spans or []:
            try:
                a, b = float(s[0]), float(s[1])
            except (TypeError, ValueError, IndexError):
                continue
            if b > a >= 0:
                clean.append([a, b])
        if clean:
            out[str(track)] = _merge(clean)
    return out


def save_claims(claims: Dict[str, List[List[float]]]) -> None:
    p = claims_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({k: _merge(v) for k, v in claims.items() if v},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _merge(spans: List[List[float]]) -> List[List[float]]:
    """Склеивает пересекающиеся отрезки, чтобы список не разрастался."""
    if not spans:
        return []
    ordered = sorted([a, b] for a, b in spans)
    out = [ordered[0]]
    for a, b in ordered[1:]:
        if a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def add_claim(track: str, start_sec: float, duration_sec: float) -> bool:
    """Запоминает заявленный отрезок. True — если он новый."""
    track = Path(str(track)).name
    a = max(0.0, float(start_sec) - MARGIN_SEC)
    b = float(start_sec) + float(duration_sec) + MARGIN_SEC
    claims = load_claims()
    existing = claims.get(track) or []
    if any(x <= a and b <= y for x, y in existing):
        return False
    claims[track] = _merge(existing + [[a, b]])
    save_claims(claims)
    logger.info("bg_claims: отрезок %s %.0f–%.0f с внесён в чёрный список",
                track, a, b)
    return True


def banned_spans(track: str) -> List[List[float]]:
    return load_claims().get(Path(str(track)).name) or []


def pick_safe_start(
    track: str,
    *,
    track_duration: float,
    window_sec: float,
    max_start: float,
    rng: Optional[random.Random] = None,
) -> float:
    """Случайное начало фона, не попадающее в заявленные отрезки.

    Собираем свободные промежутки и выбираем точку в них — это честнее, чем
    бросать кубик и надеяться: если заявлена половина трека, случайный выбор
    с отбраковкой может долго не сходиться.
    """
    r = rng or random
    hi = max(0.0, float(max_start))
    if hi <= 1.0:
        return 0.0
    spans = banned_spans(track)
    if not spans:
        return r.uniform(0.0, hi)

    # Начало небезопасно, если окно [s, s+window] пересекает любой отрезок.
    blocked = _merge([[a - float(window_sec), b] for a, b in spans])
    free: List[Tuple[float, float]] = []
    cursor = 0.0
    for a, b in blocked:
        a, b = max(0.0, a), max(0.0, b)
        if a > cursor:
            free.append((cursor, min(a, hi)))
        cursor = max(cursor, b)
        if cursor >= hi:
            break
    if cursor < hi:
        free.append((cursor, hi))
    free = [(a, b) for a, b in free if b - a > 1.0]
    if not free:
        logger.warning(
            "bg_claims: в %s не осталось чистых промежутков — беру случайное начало",
            track,
        )
        return r.uniform(0.0, hi)
    # Вес по длине промежутка: иначе короткие куски выбирались бы так же часто.
    total = sum(b - a for a, b in free)
    dice = r.uniform(0.0, total)
    for a, b in free:
        if dice <= b - a:
            return a + dice
        dice -= b - a
    a, b = free[-1]
    return r.uniform(a, b)
