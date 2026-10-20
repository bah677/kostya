"""Настройки генерации роликов, правимые из киностудии без перезапуска.

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


# Поля сгруппированы по каналам, а не по именам переменных. Раньше список
# повторял .env: «Молитв на русском» лежало рядом с «Молитв на испанском»,
# а часы премьер того же канала — через два экрана. Думают не переменными,
# а каналом: сколько он выпускает и когда.

GROUP_ORDER = ("Общее", "Русский канал", "Испанский канал", "Длинные ролики")

FIELDS: List[Field] = [
    # — общее для всех каналов —
    Field("YT_PRAYER_HOUR_MSK", "Час ночного прогона (МСК)",
          "Когда собирается вся пачка. Премьеры ставятся отдельно.",
          "number", "Общее", "3"),
    Field("YT_TALKS_ENABLED", "Разговорные ролики",
          "Разбор житейской беды с ответом из Писания. Идут после молитв.",
          "bool", "Общее", "0"),
    Field("PRAYER_TTS_ATEMPO", "Темп речи молитв",
          "0.85 — спокойнее обычного. Разговорные всегда читаются на 1.0.",
          "text", "Общее", "0.85"),
    Field("YT_PRAYER_OUTRO_SEC", "Концевая карточка, секунд",
          "Имя бота на экране в конце. 0 — выключить.",
          "text", "Общее", "2.5"),

    # — русский канал —
    Field("YT_SHORTS_COUNT", "Молитв за ночь",
          "Короткие вертикальные молитвы. Предел 12.",
          "number", "Русский канал", "5"),
    Field("YT_PRAYER_CAST_COUNT", "Из них с героями",
          "Первые N роликов пачки собираются из банка кадров, остальные стоком.",
          "number", "Русский канал", "0"),
    Field("YT_SHORTS_YOUTUBE_PREMIERE_HOURS_MSK", "Часы премьер молитв",
          "По Москве, через запятую.",
          "hours", "Русский канал", "7,12,16,19,22"),
    Field("YT_TALKS_COUNT", "Разговорных за ночь",
          "", "number", "Русский канал", "1"),
    Field("YT_TALKS_PREMIERE_HOURS", "Часы премьер разговорных",
          "По Москве. Обычно один слот в промежутке между молитвенными.",
          "hours", "Русский канал", "20"),
    Field("YT_SHORTS_ATTUNE", "Сонастройка",
          "Звучит перед молитвой. Пусто — как в .env.",
          "text", "Русский канал", ""),
    Field("YT_SHORTS_CTA", "Призыв в конце",
          "Звучит после молитвы.", "text", "Русский канал", ""),

    # — испанский канал —
    Field("YT_SHORTS_ES_COUNT", "Молитв за ночь",
          "0 — канал ночью не трогаем.",
          "number", "Испанский канал", "0"),
    Field("YT_PRAYER_CAST_COUNT_ES", "Из них с героями",
          "", "number", "Испанский канал", "0"),
    Field("YT_SHORTS_YOUTUBE_PREMIERE_HOURS_MSK_ES", "Часы премьер молитв",
          "По Мехико. Общая переменная на испанский НЕ распространяется.",
          "hours", "Испанский канал", "6,12,18,21,23"),
    Field("YT_TALKS_COUNT_ES", "Разговорных за ночь",
          "", "number", "Испанский канал", "1"),
    Field("YT_TALKS_PREMIERE_HOURS_ES", "Часы премьер разговорных",
          "По Мехико.", "hours", "Испанский канал", "20"),
    Field("YT_SHORTS_ATTUNE_ES", "Сонастройка",
          "", "text", "Испанский канал", ""),
    Field("YT_SHORTS_CTA_ES", "Призыв в конце",
          "", "text", "Испанский канал", ""),

    # — горизонталь —
    Field("YT_PRAYER_COUNT", "Горизонтальных 16:9 за ночь",
          "Длинные молитвы 4–5 минут на русском. 0 — выключены.",
          "number", "Длинные ролики", "0"),
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


# ── Что соберётся ближайшей ночью ────────────────────────────────────────
# Девятнадцать переменных плохо складываются в голове в ответ на единственный
# вопрос, который задают каждый вечер: «что выйдет завтра?». Собираем ответ
# из тех же действующих значений, по которым пойдёт прогон.


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _as_int(raw: str) -> int:
    try:
        return int(float(str(raw).strip()))
    except Exception:
        return 0


def _as_flag(raw: str) -> bool:
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "да"}


def _hours(raw: str) -> str:
    parts = [p.strip() for p in str(raw).replace(";", ",").split(",") if p.strip()]
    return ", ".join(f"{p}:00" for p in parts)


def plan() -> Dict[str, Any]:
    """Короткий человеческий ответ: что и когда выйдет."""
    vals = {f["key"]: str(f["effective"] or "") for f in describe()}
    hour = _as_int(vals.get("YT_PRAYER_HOUR_MSK") or 3)
    talks_on = _as_flag(vals.get("YT_TALKS_ENABLED"))

    channels = []
    for title, keys, tz in (
        (
            "Русский канал",
            ("YT_SHORTS_COUNT", "YT_PRAYER_CAST_COUNT",
             "YT_SHORTS_YOUTUBE_PREMIERE_HOURS_MSK", "YT_TALKS_COUNT",
             "YT_TALKS_PREMIERE_HOURS"),
            "МСК",
        ),
        (
            "Испанский канал",
            ("YT_SHORTS_ES_COUNT", "YT_PRAYER_CAST_COUNT_ES",
             "YT_SHORTS_YOUTUBE_PREMIERE_HOURS_MSK_ES", "YT_TALKS_COUNT_ES",
             "YT_TALKS_PREMIERE_HOURS_ES"),
            "по Мехико",
        ),
    ):
        k_count, k_cast, k_hours, k_talks, k_talk_hours = keys
        count = _as_int(vals.get(k_count))
        cast = min(_as_int(vals.get(k_cast)), count)
        talks = _as_int(vals.get(k_talks)) if talks_on else 0
        bits = []
        if count:
            word = _plural(count, "молитва", "молитвы", "молитв")
            bits.append(f"{count} {word}" + (f", {cast} с героями" if cast else ""))
        if talks:
            word = _plural(talks, "разговорный", "разговорных", "разговорных")
            bits.append(f"{talks} {word}")
        channels.append(
            {
                "title": title,
                "summary": ", ".join(bits) if bits else "ночью не трогаем",
                "idle": not bits,
                "total": count + talks,
                "premieres": _hours(vals.get(k_hours)) if count else "",
                "talk_premieres": _hours(vals.get(k_talk_hours)) if talks else "",
                "tz": tz,
            }
        )

    wide = _as_int(vals.get("YT_PRAYER_COUNT"))
    total = sum(c["total"] for c in channels) + wide
    return {
        "hour": hour,
        "talks_on": talks_on,
        "channels": channels,
        "wide": wide,
        "total": total,
        "headline": (
            f"В {hour}:00 соберётся {total} "
            + _plural(total, "ролик", "ролика", "роликов")
            if total
            else "Ночной прогон ничего не соберёт — все счётчики на нуле"
        ),
    }
