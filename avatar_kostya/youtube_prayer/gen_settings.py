"""Настройки генерации роликов, правимые из студии без перезапуска.

Раньше любая правка — сколько роликов за ночь, во сколько премьеры, с каким
призывом — требовала разработчика и рестарта процесса. Теперь значения
лежат в файле рядом с данными, а ``langs.setting`` смотрит туда первым.
Файл перечитывается по времени изменения, так что сохранение из интерфейса
действует на ближайший же прогон.

Почему файл, а не база: ``setting`` вызывается из синхронного кода внутри
ffmpeg-конвейера, и тащить туда асинхронные запросы к Postgres означало бы
переписать половину пайплайна. Процесс один, файл читается за микросекунды.

Переопределения сильнее .env. Пустая строка в поле — это «вернуть как в
.env», а не «выключить»: иначе из интерфейса нельзя было бы отменить
собственную правку.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {}
_CACHE_MTIME: float = -1.0


def overrides_path() -> Path:
    raw = (os.getenv("YT_PRAYER_GEN_SETTINGS_FILE") or "").strip()
    p = Path(raw) if raw else Path("data/youtube_prayer/gen_settings.json")
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[1] / p
    return p


def load_overrides() -> Dict[str, Any]:
    """Значения из файла. Перечитывается, только если файл изменился."""
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
        logger.warning("gen_settings: файл не прочитан (%s) — работаем по .env", e)
        data = {}
    with _LOCK:
        _CACHE, _CACHE_MTIME = data, mtime
    return data


def override(name: str) -> Optional[str]:
    v = load_overrides().get(name)
    if v is None:
        return None
    s = str(v).strip()
    # Пустое значение означает «как в .env», а не «пусто».
    return s or None


def save_overrides(values: Dict[str, Any]) -> Dict[str, Any]:
    """Пишет файл целиком. Пустые значения выбрасываются."""
    clean = {
        str(k): str(v).strip()
        for k, v in (values or {}).items()
        if str(v).strip() != ""
    }
    p = overrides_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    # Кэш ставим напрямую, а не через load_overrides: две записи подряд
    # укладываются в одну отметку времени, сверка по mtime их не различает,
    # и сброс настройки молча не применялся.
    global _CACHE, _CACHE_MTIME
    with _LOCK:
        _CACHE = dict(clean)
        try:
            _CACHE_MTIME = p.stat().st_mtime
        except OSError:
            _CACHE_MTIME = -1.0
    logger.info("gen_settings: сохранено %s переопределений", len(clean))
    return clean


# ── Что показываем в студии ──────────────────────────────────────────────


@dataclass(frozen=True)
class Field:
    key: str
    title: str
    hint: str = ""
    kind: str = "text"          # text | number | hours | bool
    group: str = "Общее"
    default: str = ""


FIELDS: List[Field] = [
    # — ночной прогон —
    Field("YT_PRAYER_HOUR_MSK", "Час ночного прогона (МСК)",
          "Когда собирать всю пачку. Премьеры ставятся отдельно.",
          "number", "Ночной прогон", "3"),
    Field("YT_SHORTS_COUNT", "Молитв на русском",
          "Сколько коротких молитв за ночь. Предел 12.",
          "number", "Ночной прогон", "5"),
    Field("YT_SHORTS_ES_COUNT", "Молитв на испанском",
          "0 — испанский канал не трогаем.",
          "number", "Ночной прогон", "0"),
    Field("YT_PRAYER_COUNT", "Горизонтальных 16:9",
          "Длинные молитвы 4–5 минут. 0 — выключены.",
          "number", "Ночной прогон", "0"),

    # — разговорные —
    Field("YT_TALKS_ENABLED", "Разговорные ролики",
          "Разбор житейской беды с ответом из Писания. Идёт после молитв.",
          "bool", "Разговорные", "0"),
    Field("YT_TALKS_COUNT", "Разговорных на русском",
          "", "number", "Разговорные", "1"),
    Field("YT_TALKS_COUNT_ES", "Разговорных на испанском",
          "", "number", "Разговорные", "1"),
    Field("YT_TALKS_PREMIERE_HOURS", "Часы премьер, русский",
          "По Москве. Один слот в день, в промежутке между молитвенными.",
          "hours", "Разговорные", "20"),
    Field("YT_TALKS_PREMIERE_HOURS_ES", "Часы премьер, испанский",
          "По Мехико.", "hours", "Разговорные", "20"),

    # — премьеры молитв —
    Field("YT_SHORTS_YOUTUBE_PREMIERE_HOURS_MSK", "Часы премьер, русский",
          "По Москве, через запятую.",
          "hours", "Премьеры молитв", "7,12,16,19,22"),
    Field("YT_SHORTS_YOUTUBE_PREMIERE_HOURS_MSK_ES", "Часы премьер, испанский",
          "По Мехико. Общая переменная на испанский НЕ распространяется.",
          "hours", "Премьеры молитв", "6,12,18,21,23"),

    # — герои —
    Field("YT_PRAYER_CAST_COUNT", "Роликов с героями, русский",
          "Первые N роликов пачки собираются из банка, остальные стоком.",
          "number", "Герои", "0"),
    Field("YT_PRAYER_CAST_COUNT_ES", "Роликов с героями, испанский",
          "", "number", "Герои", "0"),

    # — голос —
    Field("PRAYER_TTS_ATEMPO", "Темп речи молитв",
          "0.85 — спокойнее обычного. Разговорные всегда читаются на 1.0.",
          "text", "Голос", "0.85"),
    Field("YT_PRAYER_OUTRO_SEC", "Концевая карточка, секунд",
          "0 — выключить. На столько же удлиняется аудио.",
          "text", "Голос", "2.5"),

    # — тексты —
    Field("YT_SHORTS_ATTUNE", "Сонастройка, русский",
          "Звучит перед молитвой. Пусто — как в .env.",
          "text", "Тексты", ""),
    Field("YT_SHORTS_CTA", "Призыв в конце, русский",
          "Звучит после молитвы.", "text", "Тексты", ""),
    Field("YT_SHORTS_ATTUNE_ES", "Сонастройка, испанский",
          "", "text", "Тексты", ""),
    Field("YT_SHORTS_CTA_ES", "Призыв в конце, испанский",
          "", "text", "Тексты", ""),
]


def _env_value(key: str) -> str:
    try:
        from config import config as cfg

        if hasattr(cfg, key):
            return str(getattr(cfg, key))
    except Exception:
        pass
    return str(os.getenv(key) or "")


def describe() -> List[Dict[str, Any]]:
    """Поля со значениями: что в .env, что переопределено, что действует."""
    ov = load_overrides()
    out: List[Dict[str, Any]] = []
    for f in FIELDS:
        env_val = _env_value(f.key)
        over = str(ov.get(f.key) or "")
        out.append(
            {
                "key": f.key,
                "title": f.title,
                "hint": f.hint,
                "kind": f.kind,
                "group": f.group,
                "env": env_val,
                "default": f.default,
                "value": over,
                "effective": over or env_val or f.default,
                "overridden": bool(over),
            }
        )
    return out
