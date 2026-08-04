"""История выбранных трендов — чтобы не повторять темы день за днём."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Sequence, Set
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_MSK = ZoneInfo("Europe/Moscow")
_HISTORY_FILE = "used_trends.json"


def normalize_trend_key(text: str) -> str:
    t = (text or "").casefold().strip()
    t = t.replace("ё", "е")
    t = re.sub(r"[^\wа-яa-z0-9]+", " ", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _token_set(key: str) -> Set[str]:
    return {w for w in key.split() if len(w) >= 3}


def trends_similar(a: str, b: str) -> bool:
    """Грубое совпадение: точное / один содержит другой / сильное пересечение токенов."""
    ka, kb = normalize_trend_key(a), normalize_trend_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    if ka in kb or kb in ka:
        return True
    ta, tb = _token_set(ka), _token_set(kb)
    if not ta or not tb:
        return False
    inter = ta & tb
    if not inter:
        return False
    # «тревога беспокойство» vs «тревога» — пересечение достаточно
    ratio = len(inter) / min(len(ta), len(tb))
    return ratio >= 0.6


def load_recent_trends(
    work_root: Path,
    *,
    history_days: int = 14,
    as_of: date | None = None,
) -> List[str]:
    """
    Темы из used_trends.json + done.json за последние history_days.
    Возвращает список строк (как сохраняли), без жёсткой дедупликации по смыслу.
    """
    days = max(1, min(90, int(history_days)))
    as_of = as_of or datetime.now(_MSK).date()
    cutoff = as_of - timedelta(days=days)
    collected: List[str] = []
    seen_keys: Set[str] = set()

    def _add(theme: str, day_s: str | None = None) -> None:
        theme = (theme or "").strip()
        if not theme:
            return
        if day_s:
            try:
                d = date.fromisoformat(day_s[:10])
            except ValueError:
                d = None
            if d is not None and d < cutoff:
                return
        key = normalize_trend_key(theme)
        if not key or key in seen_keys:
            return
        # отсев почти-дублей внутри окна
        if any(trends_similar(theme, x) for x in collected):
            return
        seen_keys.add(key)
        collected.append(theme)

    hist_path = work_root / _HISTORY_FILE
    if hist_path.is_file():
        try:
            data = json.loads(hist_path.read_text(encoding="utf-8"))
            entries = data.get("entries") if isinstance(data, dict) else None
            if isinstance(entries, list):
                for ent in entries:
                    if not isinstance(ent, dict):
                        continue
                    day_s = str(ent.get("day") or "")
                    for th in ent.get("themes") or []:
                        _add(str(th), day_s)
        except Exception as e:
            logger.warning("used_trends.json read failed: %s", e)

    if work_root.is_dir():
        for child in sorted(work_root.iterdir()):
            if not child.is_dir():
                continue
            done = child / "done.json"
            if not done.is_file():
                continue
            day_s = child.name
            try:
                payload = json.loads(done.read_text(encoding="utf-8"))
            except Exception:
                continue
            themes = payload.get("themes") if isinstance(payload, dict) else None
            if not isinstance(themes, list):
                continue
            for th in themes:
                _add(str(th), day_s)

    logger.info(
        "trend history: %s themes in last %s days (as_of=%s)",
        len(collected),
        days,
        as_of.isoformat(),
    )
    return collected


def append_used_trends(
    work_root: Path,
    *,
    day: str,
    themes: Sequence[str],
) -> None:
    """Дописывает день в used_trends.json (перезаписывает запись того же day)."""
    work_root.mkdir(parents=True, exist_ok=True)
    path = work_root / _HISTORY_FILE
    entries: List[dict] = []
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("entries"), list):
                entries = [e for e in data["entries"] if isinstance(e, dict)]
        except Exception:
            entries = []
    entries = [e for e in entries if str(e.get("day") or "") != day]
    clean = [str(t).strip() for t in themes if str(t).strip()]
    entries.append(
        {
            "day": day,
            "themes": clean,
            "saved_at": datetime.now(_MSK).isoformat(),
        }
    )
    # храним ~90 дней
    def _day_key(e: dict) -> str:
        return str(e.get("day") or "")

    entries.sort(key=_day_key)
    if len(entries) > 90:
        entries = entries[-90:]
    path.write_text(
        json.dumps({"entries": entries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def filter_out_recent(
    candidates: Iterable[str],
    recent: Sequence[str],
) -> List[str]:
    out: List[str] = []
    for c in candidates:
        c = (c or "").strip()
        if not c:
            continue
        if any(trends_similar(c, r) for r in recent):
            continue
        out.append(c)
    return out
