"""Дерево объектов контекста веб-студии Кости.

Структура завязана на метаданные RAG (``product`` / ``content_type`` /
``content_category``), а не на уроки курса Юлии.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from course.products import active_product_id, product_chroma_names, product_display_name

logger = logging.getLogger(__name__)

# id → (название, подсказка, фильтры Chroma)
# content_types / content_categories — точные значения из метаданных индекса.
FACET_SPECS: Dict[str, Dict[str, Any]] = {
    "efir": {
        "name": "Эфиры",
        "hint": "Выступления и эфиры",
        "content_types": ("Эфир", "Эфиры"),
        "group": "materials",
    },
    "molitva": {
        "name": "Молитвы",
        "hint": "Утренние и клубные молитвы",
        "content_types": ("Молитва", "Молитвы"),
        "group": "materials",
    },
    "pokayanie": {
        "name": "Покаяние",
        "hint": "Встречи покаяния",
        "content_types": ("Покаяние",),
        "group": "materials",
    },
    "qa": {
        "name": "Вопрос-ответ",
        "hint": "Эфиры вопрос–ответ",
        "content_types": ("Вопрос-ответ",),
        "group": "materials",
    },
    "meeting": {
        "name": "Встречи клуба",
        "hint": "Встречи «Разговоры с Богом»",
        "content_types": ("встреча клуба", "встреча"),
        "group": "materials",
    },
    "podcast": {
        "name": "Подкасты",
        "hint": "Подкасты",
        "content_types": ("Подкасты",),
        "group": "materials",
    },
    "product": {
        "name": "О клубе",
        "hint": "Описание продукта и клуба",
        "content_types": ("Описание продукта",),
        "group": "materials",
    },
    "stories": {
        "name": "Сценарии сториз",
        "hint": "Готовые сценарии сторис",
        "content_types": ("Сценарии сториз",),
        "group": "materials",
    },
    "testimonial": {
        "name": "Отзывы",
        "hint": "Отзывы участников",
        "content_types": ("Отзывы клиентов",),
        "content_categories": ("testimonial",),
        "group": "audience",
    },
    "dialog": {
        "name": "Диалоги в чатах",
        "hint": "Переписка и живые формулировки из групп",
        "content_categories": ("dialog",),
        "group": "audience",
    },
}

FACET_KEYS = tuple(FACET_SPECS.keys())
FACET_NAMES = {k: str(v["name"]) for k, v in FACET_SPECS.items()}
FACET_FILTERS: Dict[str, Dict[str, Any]] = {
    k: {
        key: list(val)
        for key, val in (
            ("content_types", spec.get("content_types")),
            ("content_categories", spec.get("content_categories")),
        )
        if val
    }
    for k, spec in FACET_SPECS.items()
}

# Старые id живого чата Юлии → фасеты Кости (сохранённые чаты).
LIVE_TO_FACET = {
    "chat": "dialog",
    "client": "dialog",
    "expert": "dialog",
    "dialog": "dialog",
    "expert_reply": "dialog",
    "testimonial": "testimonial",
}

# Совместимость импортов pipeline (раньше LIVE_*).
LIVE_KEYS = tuple(LIVE_TO_FACET.keys())
LIVE_NAMES = {k: FACET_NAMES.get(LIVE_TO_FACET[k], k) for k in LIVE_KEYS}
LIVE_FILTERS: Dict[str, Dict[str, Any]] = {
    k: dict(FACET_FILTERS.get(LIVE_TO_FACET[k]) or {}) for k in LIVE_KEYS
}
LIVE_ALIASES = {"dialog": "chat", "expert_reply": "expert"}

_GROUP_TITLES = {
    "materials": "Материалы Константина",
    "audience": "Аудитория",
}


@dataclass(frozen=True)
class ObjectRef:
    """`facet:efir`, `source:<uuid>`, `live:chat` (алиас), …"""

    kind: str  # facet | source | lesson | passport | live
    value: str

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.value}"

    def as_facet_key(self) -> Optional[str]:
        if self.kind == "facet" and self.value in FACET_SPECS:
            return self.value
        if self.kind == "live":
            mapped = LIVE_TO_FACET.get(self.value)
            if mapped in FACET_SPECS:
                return mapped
        return None


def parse_object_id(raw: str) -> Optional[ObjectRef]:
    text = (raw or "").strip()
    if ":" not in text:
        return None
    kind, value = text.split(":", 1)
    kind = kind.strip().lower()
    value = value.strip()
    if not value:
        return None
    if kind == "facet" and value in FACET_SPECS:
        return ObjectRef("facet", value)
    if kind == "live":
        value = LIVE_ALIASES.get(value, value)
        value = LIVE_TO_FACET.get(value, value)
        if value in FACET_SPECS:
            # Нормализуем live → facet, чтобы в чате хранились костины id.
            return ObjectRef("facet", value)
        return None
    if kind == "source":
        return ObjectRef("source", value)
    if kind == "lesson":
        return ObjectRef("lesson", value)
    if kind == "passport":
        return ObjectRef("passport", value)
    return None


def parse_object_ids(raw: Sequence[str]) -> List[ObjectRef]:
    out: List[ObjectRef] = []
    seen: set[str] = set()
    for item in raw or []:
        ref = parse_object_id(str(item))
        if ref and ref.id not in seen:
            seen.add(ref.id)
            out.append(ref)
    return out


def source_label(src: Dict[str, Any]) -> str:
    """Совместимость со старым pipeline (course sources)."""
    title = (src.get("title") or "").strip()
    kind = str(src.get("kind") or "").strip()
    if title:
        return title[:160]
    return (kind or "Материал")[:160]


def facet_search_filter(facet_keys: Sequence[str]) -> Optional[Dict[str, Any]]:
    """Общий where-extra для нескольких выбранных фасетов ($or)."""
    clauses: List[dict] = []
    for key in facet_keys:
        flt = FACET_FILTERS.get(key) or {}
        types = [str(x) for x in (flt.get("content_types") or []) if str(x).strip()]
        cats = [str(x) for x in (flt.get("content_categories") or []) if str(x).strip()]
        parts: List[dict] = []
        if types:
            parts.append({"content_type": {"$in": types}})
        if cats:
            parts.append({"content_category": {"$in": cats}})
        if not parts:
            continue
        if len(parts) == 1:
            clauses.append(parts[0])
        else:
            clauses.append({"$or": parts})
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$or": clauses}


def _count_facets(app) -> Dict[str, int]:
    out = {k: 0 for k in FACET_KEYS}
    rag = getattr(app, "rag_stack", None)
    if rag is None:
        return out
    try:
        coll = rag.vectors.expert_collection
        raw = coll.get(include=["metadatas"], limit=20_000)
    except Exception as e:
        logger.warning("facet counts: %s", e)
        return out
    type_to_facets: Dict[str, List[str]] = {}
    cat_to_facets: Dict[str, List[str]] = {}
    for key, spec in FACET_SPECS.items():
        for t in spec.get("content_types") or ():
            type_to_facets.setdefault(str(t), []).append(key)
        for c in spec.get("content_categories") or ():
            cat_to_facets.setdefault(str(c), []).append(key)

    allowed_products = set(product_chroma_names(active_product_id()))
    for meta in raw.get("metadatas") or []:
        m = meta or {}
        prod = str(m.get("product") or "").strip()
        if allowed_products and prod and prod not in allowed_products:
            continue
        matched: set[str] = set()
        ctype = str(m.get("content_type") or "").strip()
        if ctype:
            matched.update(type_to_facets.get(ctype) or ())
        cat = str(m.get("content_category") or "").strip()
        if cat:
            matched.update(cat_to_facets.get(cat) or ())
        for key in matched:
            out[key] += 1
    return out


async def build_tree(app) -> Dict[str, Any]:
    """Группы: материалы по типу + аудитория (диалоги / отзывы)."""
    pid = active_product_id()
    counts = _count_facets(app)

    buckets: Dict[str, List[Dict[str, Any]]] = {gid: [] for gid in _GROUP_TITLES}
    for key, spec in FACET_SPECS.items():
        n = int(counts.get(key) or 0)
        if n <= 0:
            continue
        gid = str(spec.get("group") or "materials")
        buckets.setdefault(gid, []).append(
            {
                "id": f"facet:{key}",
                "kind": "facet",
                "name": spec["name"],
                "meta": f"{n} фрагментов · {spec['hint']}",
                "status": "ready",
                "selectable": True,
                "facet": key,
            }
        )

    groups: List[Dict[str, Any]] = []
    for gid, title in _GROUP_TITLES.items():
        items = buckets.get(gid) or []
        if items:
            groups.append({"id": gid, "title": title, "items": items})

    return {
        "product": {"id": pid, "name": product_display_name(pid)},
        "groups": groups,
    }


def object_names(tree: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}

    def _walk(items: Sequence[Dict[str, Any]]) -> None:
        for item in items or []:
            if item.get("id"):
                out[str(item["id"])] = str(item.get("name") or item["id"])
            _walk(item.get("children") or [])

    for group in tree.get("groups") or []:
        _walk(group.get("items") or [])
        for mod in group.get("modules") or []:
            _walk(mod.get("lessons") or [])
    return out
