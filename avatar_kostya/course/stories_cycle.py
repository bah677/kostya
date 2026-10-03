"""Этапы прогрева воронки: общее описание, без правил формата контента."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Дефолтный набор — сид, если в БД ещё ничего не сохраняли.
DEFAULT_STAGES: List[Tuple[str, str, str]] = [
    (
        "warmup",
        "Нативный прогрев",
        "До открытия продаж: лицо, боль, метод, узнавание. "
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
        "Окно открыто: оффер, разбор возражений, соцдоказ, куда писать, до когда.",
    ),
    (
        "aftercare",
        "После закрытия",
        "2–3 недели после последнего окна: как идёт у тех, кто уже внутри, "
        "разборы, тепло. Плавно перетекает снова в нативный прогрев — круг.",
    ),
]

# Совместимость со старым кодом (кортежи id/title/hint).
STAGES: List[Tuple[str, str, str]] = list(DEFAULT_STAGES)
STAGE_IDS = tuple(s[0] for s in DEFAULT_STAGES)
DEFAULT_STAGE = "warmup"
STAGES_KEY = "funnel_stages"
_LEGACY_TEXTS_KEY = "stories_stage_texts"


def default_stages() -> List[Dict[str, str]]:
    return [
        {"id": sid, "title": title, "description": desc}
        for sid, title, desc in DEFAULT_STAGES
    ]


def _slug_id(title: str, used: set[str]) -> str:
    raw = (title or "").strip().casefold()
    # латиница/цифры; иначе короткий uuid
    base = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    if not base or not re.search(r"[a-z]", base):
        base = "stage_" + uuid.uuid4().hex[:8]
    cand = base[:40]
    n = 2
    while cand in used:
        cand = f"{base[:36]}_{n}"
        n += 1
    return cand


def decode_stages(raw: Any) -> List[Dict[str, str]]:
    """Сырое значение content_settings → список этапов."""
    if isinstance(raw, (bytes, memoryview)):
        raw = bytes(raw).decode("utf-8")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return []
    rows: List[Any]
    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict) and isinstance(raw.get("stages"), list):
        rows = raw["stages"]
    else:
        return []
    out: List[Dict[str, str]] = []
    used: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()[:200]
        desc = str(
            row.get("description") or row.get("hint") or row.get("desc") or ""
        ).strip()[:4000]
        sid = str(row.get("id") or "").strip().lower()
        sid = re.sub(r"[^a-z0-9_\-]+", "_", sid).strip("_")[:48]
        if not title and not desc:
            continue
        if not title:
            title = sid or "Этап"
        if not sid or sid in used:
            sid = _slug_id(title, used)
        used.add(sid)
        out.append({"id": sid, "title": title, "description": desc})
    return out


def _from_legacy_texts(raw: Any) -> List[Dict[str, str]]:
    """Миграция со старого stories_stage_texts {id: {title, hint, rules}}."""
    if not isinstance(raw, dict):
        return []
    out: List[Dict[str, str]] = []
    seen: set[str] = set()
    for sid, title, desc in DEFAULT_STAGES:
        row = raw.get(sid) if isinstance(raw.get(sid), dict) else {}
        out.append(
            {
                "id": sid,
                "title": str((row or {}).get("title") or title).strip() or title,
                "description": str(
                    (row or {}).get("hint") or (row or {}).get("description") or desc
                ).strip()
                or desc,
            }
        )
        seen.add(sid)
    for sid, row in raw.items():
        if sid in seen or not isinstance(row, dict):
            continue
        title = str(row.get("title") or sid).strip()
        desc = str(row.get("hint") or row.get("description") or "").strip()
        if title or desc:
            out.append({"id": str(sid), "title": title or str(sid), "description": desc})
    return out


def normalize_stage(
    value: str, stages: Optional[Sequence[Dict[str, str]]] = None
) -> str:
    raw = (value or "").strip().lower()
    rows = list(stages) if stages is not None else default_stages()
    ids = {str(s.get("id") or "") for s in rows}
    if raw in ids:
        return raw
    return str(rows[0]["id"]) if rows else DEFAULT_STAGE


def stage_by_id(
    stage_id: str, stages: Optional[Sequence[Dict[str, str]]] = None
) -> Dict[str, str]:
    rows = list(stages) if stages is not None else default_stages()
    sid = normalize_stage(stage_id, rows)
    for s in rows:
        if s["id"] == sid:
            return s
    return rows[0] if rows else {"id": DEFAULT_STAGE, "title": "", "description": ""}


def stage_title(
    stage_id: str, stages: Optional[Sequence[Dict[str, str]]] = None
) -> str:
    return stage_by_id(stage_id, stages).get("title") or ""


def stage_description(
    stage_id: str, stages: Optional[Sequence[Dict[str, str]]] = None
) -> str:
    return stage_by_id(stage_id, stages).get("description") or ""


def stage_prompt(
    stage_id: str,
    stages: Optional[Sequence[Dict[str, str]]] = None,
    texts: Optional[Dict[str, Dict[str, str]]] = None,
) -> str:
    """Тексты stages — предпочтительно; texts — совместимость со старым вызовом."""
    if stages is None and texts:
        stages = [
            {
                "id": sid,
                "title": str((row or {}).get("title") or sid),
                "description": str(
                    (row or {}).get("description")
                    or (row or {}).get("hint")
                    or ""
                ),
            }
            for sid, row in texts.items()
        ]
    row = stage_by_id(stage_id, stages)
    title = row.get("title") or "этап"
    desc = (row.get("description") or "").strip()
    if desc:
        return f"Этап прогрева: {title}. {desc}"
    return f"Этап прогрева: {title}."


def writer_stories_rules(
    stage_id: str = "",
    texts: Optional[Dict[str, Dict[str, str]]] = None,
) -> str:
    """Устарело: правила формата живут в скилле вида контента.

    Оставлено для старых вызовов (course/writer) — тонкая отсылка к этапу.
    """
    _ = texts
    return (
        "Учитывай этап прогрева из контекста задачи: жёсткость оффера и CTA "
        "подстраивай под этап, не выдумывай цены и дедлайны вне паспорта запуска."
    )


async def load_stages(stor) -> List[Dict[str, str]]:
    from course.products import active_product_id

    pid = active_product_id()
    raw = await stor.get_content_setting(pid, STAGES_KEY)
    rows = decode_stages(raw)
    if rows:
        return rows
    legacy = await stor.get_content_setting(pid, _LEGACY_TEXTS_KEY)
    migrated = _from_legacy_texts(legacy)
    return migrated or default_stages()


async def save_stages(
    stor, payload: Any, *, user_id: int = 0
) -> List[Dict[str, str]]:
    """Сохраняет полный список этапов (add/delete/reorder через замену списка)."""
    from course.products import active_product_id

    _ = user_id
    if isinstance(payload, dict) and "stages" in payload:
        payload = payload["stages"]
    # Старый формат {id: {title, hint}} → список
    if isinstance(payload, dict) and not isinstance(payload.get("stages"), list):
        as_list = []
        for sid, row in payload.items():
            if sid == "stages" or not isinstance(row, dict):
                continue
            as_list.append(
                {
                    "id": sid,
                    "title": row.get("title"),
                    "description": row.get("description") or row.get("hint"),
                }
            )
        payload = as_list
    rows = decode_stages(payload if isinstance(payload, list) else [])
    if not rows:
        rows = default_stages()
    await stor.set_content_setting(active_product_id(), STAGES_KEY, rows)
    return rows


# Алиасы под старые имена в web/api и pipeline
async def load_stage_texts(stor) -> Dict[str, Dict[str, str]]:
    stages = await load_stages(stor)
    return {
        s["id"]: {
            "title": s["title"],
            "hint": s["description"],
            "description": s["description"],
            "rules": "",
        }
        for s in stages
    }


async def save_stage_texts(stor, payload: Dict[str, Any], *, user_id: int = 0):
    return await save_stages(stor, payload, user_id=user_id)


def resolved_stages(
    overrides: Optional[Dict[str, Dict[str, str]]] = None,
) -> List[Dict[str, str]]:
    """Для bootstrap без stor: дефолты или словарь texts → список с hint."""
    if not overrides:
        return [
            {"id": s["id"], "title": s["title"], "hint": s["description"], "description": s["description"]}
            for s in default_stages()
        ]
    # overrides как id→fields или уже список через decode
    if isinstance(overrides, list):
        rows = decode_stages(overrides)
    else:
        rows = []
        for sid, title, desc in DEFAULT_STAGES:
            patch = overrides.get(sid) or {}
            rows.append(
                {
                    "id": sid,
                    "title": str(patch.get("title") or title).strip() or title,
                    "description": str(
                        patch.get("description") or patch.get("hint") or desc
                    ).strip()
                    or desc,
                }
            )
        for sid, patch in overrides.items():
            if any(r["id"] == sid for r in rows) or not isinstance(patch, dict):
                continue
            rows.append(
                {
                    "id": str(sid),
                    "title": str(patch.get("title") or sid).strip(),
                    "description": str(
                        patch.get("description") or patch.get("hint") or ""
                    ).strip(),
                }
            )
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "hint": r["description"],
            "description": r["description"],
        }
        for r in rows
    ]


def decode_stage_texts(raw: Any) -> Dict[str, Dict[str, str]]:
    rows = decode_stages(raw) or _from_legacy_texts(raw)
    if not rows and isinstance(raw, dict):
        rows = _from_legacy_texts(raw)
    return {
        r["id"]: {
            "title": r["title"],
            "hint": r["description"],
            "description": r["description"],
            "rules": "",
        }
        for r in rows
    }
