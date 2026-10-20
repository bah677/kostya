"""Правки героев и сцен из киностудии — файл рядом с данными, без перезапуска.

ДНК героев и список сцен раньше лежали только в коде: поменять оттенок
света или добавить кадр мог лишь разработчик. Теперь правки пишутся в
data/youtube_prayer/cast.json, а cast.py читает их поверх значений по
умолчанию — как gen_settings для настроек генерации.

Файл перечитывается по времени изменения. Пустое поле означает «как в
коде», а не «пусто»: иначе из интерфейса нельзя было бы отменить свою же
правку. То же для списка сцен — пустой список возвращает исходный набор.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {}
_CACHE_MTIME: float = -1.0

# Что разрешено править. Ключи совпадают с именами в cast.py.
# Героинь может быть несколько, и их ключи живут там же — спрашиваем,
# а не дублируем список, иначе третья героиня молча не сохранится.
_BASE_TEXT_FIELDS = ("style", "jesus")


def text_fields() -> tuple:
    try:
        from youtube_prayer.cast import HEROINES

        return _BASE_TEXT_FIELDS + tuple(h.store_key for h in HEROINES)
    except Exception:
        return _BASE_TEXT_FIELDS + ("woman",)


TEXT_FIELDS = _BASE_TEXT_FIELDS + ("woman",)  # для старого кода
SCENE_FIELDS = ("key", "who", "shot", "prompt", "topics")
WHO_VALUES = ("woman", "jesus", "both")


def overrides_path() -> Path:
    raw = (os.getenv("YT_PRAYER_CAST_FILE") or "").strip()
    p = Path(raw) if raw else Path("data/youtube_prayer/cast.json")
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[1] / p
    return p


def load() -> Dict[str, Any]:
    global _CACHE, _CACHE_MTIME
    p = overrides_path()
    try:
        mtime = p.stat().st_mtime
    except FileNotFoundError:
        with _LOCK:
            _CACHE, _CACHE_MTIME = {}, -1.0
        return {}
    if mtime == _CACHE_MTIME:
        return _CACHE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except Exception as e:
        logger.warning("cast_store: файл не прочитан (%s) — работаем по коду", e)
        data = {}
    with _LOCK:
        _CACHE, _CACHE_MTIME = data, mtime
    return data


def text(name: str) -> Optional[str]:
    v = load().get(name)
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def scenes_raw() -> Optional[List[Dict[str, Any]]]:
    """Сцены из файла. None — значит берём набор из кода."""
    rows = load().get("scenes")
    if not isinstance(rows, list) or not rows:
        return None
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip()
        if not key:
            continue
        who = str(row.get("who") or "").strip()
        topics = row.get("topics")
        if isinstance(topics, str):
            topics = [t.strip() for t in topics.split(",")]
        out.append(
            {
                "key": key,
                "who": who if who in WHO_VALUES else "woman",
                "shot": str(row.get("shot") or "").strip(),
                "prompt": str(row.get("prompt") or "").strip(),
                "topics": [str(t).strip() for t in (topics or []) if str(t).strip()],
            }
        )
    return out or None


def save(data: Dict[str, Any]) -> Dict[str, Any]:
    """Пишет файл целиком. Пустые значения выбрасываются."""
    clean: Dict[str, Any] = {}
    for name in text_fields():
        v = str((data or {}).get(name) or "").strip()
        if v:
            clean[name] = v
    rows = (data or {}).get("scenes")
    if isinstance(rows, list) and rows:
        kept: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_key = str(row.get("key") or "").strip()
            # Ключ — имя файла кадра в банке, поэтому только латиница и цифры.
            # Молча подчищать нельзя: кадры уже лежат под старым именем, и
            # переименование ключа оторвало бы их от сцены.
            key = "".join(
                c for c in raw_key.lower() if c.isascii() and (c.isalnum() or c in "_-")
            )
            if not key or key != raw_key.lower():
                shown = raw_key or "пусто"
                raise ValueError(
                    f"Ключ кадра «{shown}» не годится: "
                    "только латиница, цифры, дефис и подчёркивание"
                )
            if key in seen:
                raise ValueError(f"Ключ кадра «{key}» повторяется")
            seen.add(key)
            who = str(row.get("who") or "").strip()
            topics = row.get("topics")
            if isinstance(topics, str):
                topics = [t.strip() for t in topics.split(",")]
            kept.append(
                {
                    "key": key,
                    "who": who if who in WHO_VALUES else "woman",
                    "shot": str(row.get("shot") or "").strip(),
                    "prompt": str(row.get("prompt") or "").strip(),
                    "topics": [str(t).strip() for t in (topics or []) if str(t).strip()],
                }
            )
        if kept:
            clean["scenes"] = kept

    p = overrides_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    # Кэш ставим напрямую: две записи подряд укладываются в одну отметку
    # времени, и сверка по mtime их не различает.
    global _CACHE, _CACHE_MTIME
    with _LOCK:
        _CACHE = dict(clean)
        try:
            _CACHE_MTIME = p.stat().st_mtime
        except OSError:
            _CACHE_MTIME = -1.0
    logger.info(
        "cast_store: сохранено (%s текстов, %s сцен)",
        sum(1 for f in text_fields() if f in clean),
        len(clean.get("scenes") or []),
    )
    return clean
