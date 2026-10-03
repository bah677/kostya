"""Четыре этапа сторис вокруг запуска — цикл, а не разовая кампания."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

STAGES: List[Tuple[str, str, str]] = [
    (
        "warmup",
        "Нативный прогрев",
        "До открытия продаж: лицо, боль, метод из уроков, узнавание. "
        "Почти без оффера. CTA мягкий: директ / «напиши», не цена и не дедлайн.",
    ),
    (
        "prelaunch",
        "Предзапуск",
        "Закрытые группы, ранний доступ, лист ожидания. "
        "Намекаем на набор, собираем тёплых. Цена — только если эксперт явно просит.",
    ),
    (
        "launch",
        "Активные продажи",
        "Окно открыто: оффер, разбор возражений, соцдоказ, куда писать, до когда. "
        "Сторис короткие, CTA в конце ленты дня, не в каждом кадре.",
    ),
    (
        "aftercare",
        "После закрытия",
        "2–3 недели после последнего окна: как идёт у тех, кто уже внутри, "
        "разборы, тепло. Плавно перетекает снова в нативный прогрев — круг.",
    ),
]

STAGE_IDS = tuple(s[0] for s in STAGES)
DEFAULT_STAGE = "warmup"
STAGE_TEXTS_KEY = "stories_stage_texts"

_BY_ID: Dict[str, Tuple[str, str, str]] = {s[0]: s for s in STAGES}

# Правила для WRITE (сторис) — правятся в студии, хранятся в content_settings.
DEFAULT_STAGE_RULES: Dict[str, str] = {
    "warmup": (
        "Не продавай поток. 1–2 кадра — живая сцена или боль, затем кусок метода. "
        "Интерактив обязателен (опрос/вопрос/слайдер). CTA максимум «напиши мне»."
    ),
    "prelaunch": (
        "Можно сказать, что набор близко. Без полной цены, если её нет в паспорте запуска. "
        "Собери интерес: «напиши слово», закрытый созвон, лист."
    ),
    "launch": (
        "Последние 1–2 кадра — оффер и куда писать (из паспорта запуска). "
        "Возражение — отдельный кадр, не мешай с хуком. Не обещай того, чего нет в паспорте."
    ),
    "aftercare": (
        "Герой дня — человек, который уже внутри (без имён и узнаваемых деталей, если эксперт не дал согласие). "
        "Покажи процесс и тепло. В конце ленты можно мягко вернуться к прогреву, без жёсткого дедлайна."
    ),
}

_STORIES_FRAME = (
    "Раскадровка на день, не один абзац: 8–12 кадров. "
    "На кадр: текст НА ЭКРАНЕ (до 8–12 слов), что сказать голосом (1–2 фразы), "
    "стикер если есть, роль кадра (хук/боль/метод/интерактив/доказ/CTA).\n"
)


def normalize_stage(value: str) -> str:
    raw = (value or "").strip().lower()
    return raw if raw in _BY_ID else DEFAULT_STAGE


def decode_stage_texts(raw: Any) -> Dict[str, Dict[str, str]]:
    """Сырое значение из content_settings → {stage_id: {title, hint, rules}}."""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for sid in STAGE_IDS:
        row = raw.get(sid)
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        hint = str(row.get("hint") or "").strip()
        rules = str(row.get("rules") or "").strip()
        if title or hint or rules:
            out[sid] = {"title": title, "hint": hint, "rules": rules}
    return out


def resolved_stages(overrides: Optional[Dict[str, Dict[str, str]]] = None) -> List[Dict[str, str]]:
    """Этапы для API/UI: дефолты + сохранённые правки."""
    ov = overrides or {}
    rows: List[Dict[str, str]] = []
    for sid, title, hint in STAGES:
        patch = ov.get(sid) or {}
        rows.append(
            {
                "id": sid,
                "title": (patch.get("title") or title).strip() or title,
                "hint": (patch.get("hint") or hint).strip() or hint,
                "rules": (patch.get("rules") or DEFAULT_STAGE_RULES.get(sid) or "").strip(),
            }
        )
    return rows


def stage_title(stage_id: str, texts: Optional[Dict[str, Dict[str, str]]] = None) -> str:
    sid = normalize_stage(stage_id)
    if texts and (texts.get(sid) or {}).get("title"):
        return str(texts[sid]["title"]).strip()
    row = _BY_ID.get(sid)
    return row[1] if row else STAGES[0][1]


def stage_hint(stage_id: str, texts: Optional[Dict[str, Dict[str, str]]] = None) -> str:
    sid = normalize_stage(stage_id)
    if texts and (texts.get(sid) or {}).get("hint"):
        return str(texts[sid]["hint"]).strip()
    row = _BY_ID.get(sid)
    return row[2] if row else STAGES[0][2]


def stage_prompt(stage_id: str, texts: Optional[Dict[str, Dict[str, str]]] = None) -> str:
    sid = normalize_stage(stage_id)
    return f"Этап сторис: {stage_title(sid, texts)}. {stage_hint(sid, texts)}"


def writer_stories_rules(
    stage_id: str, texts: Optional[Dict[str, Dict[str, str]]] = None
) -> str:
    sid = normalize_stage(stage_id)
    extra = ""
    if texts and (texts.get(sid) or {}).get("rules"):
        extra = str(texts[sid]["rules"]).strip()
    else:
        extra = DEFAULT_STAGE_RULES.get(sid, "")
    return _STORIES_FRAME + extra


async def load_stage_texts(stor) -> Dict[str, Dict[str, str]]:
    from course.products import active_product_id

    raw = await stor.get_content_setting(active_product_id(), STAGE_TEXTS_KEY)
    return decode_stage_texts(raw)


async def save_stage_texts(
    stor, payload: Dict[str, Any], *, user_id: int = 0
) -> Dict[str, Dict[str, str]]:
    """Сохраняет правки этапов. Пустые поля = вернуть дефолт (не хранить)."""
    from course.products import active_product_id

    cleaned: Dict[str, Dict[str, str]] = {}
    src = payload if isinstance(payload, dict) else {}
    for sid, def_title, def_hint in STAGES:
        row = src.get(sid) if isinstance(src.get(sid), dict) else {}
        title = str((row or {}).get("title") or "").strip()
        hint = str((row or {}).get("hint") or "").strip()
        rules = str((row or {}).get("rules") or "").strip()
        # Не дублируем дефолты в БД — только отличия.
        patch: Dict[str, str] = {}
        if title and title != def_title:
            patch["title"] = title[:200]
        if hint and hint != def_hint:
            patch["hint"] = hint[:2000]
        def_rules = DEFAULT_STAGE_RULES.get(sid, "")
        if rules and rules != def_rules:
            patch["rules"] = rules[:4000]
        if patch:
            cleaned[sid] = patch
    await stor.set_content_setting(active_product_id(), STAGE_TEXTS_KEY, cleaned)
    return decode_stage_texts(cleaned)
