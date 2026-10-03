"""Дерево объектов веб-студии Кости.

Архитектура (не курс Юлии):

  тип (Эфиры / Молитвы / …)
    └── конкретная запись (source из Chroma)
          └── исходник: склейка чанков + transcript телемоста, если есть

Идентификаторы:
  ``facet:<тип>`` — весь тип целиком
  ``mat:<тип>:<hash>`` — одна запись (эфир / молитва / …)
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import unquote

from course.products import active_product_id, product_chroma_names, product_display_name

logger = logging.getLogger(__name__)

_MEETING_ID_RE = re.compile(r"telemost\.yandex\.ru/j/(\d+)", re.I)

# Тип → человекочитаемое имя + какие content_type из индекса сюда входят.
FACET_SPECS: Dict[str, Dict[str, Any]] = {
    "efir": {
        "name": "Эфиры",
        "hint": "Эфиры и выступления Константина",
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
    "podcast": {
        "name": "Подкасты",
        "hint": "Подкасты и голосовые размышления",
        "content_types": ("Подкасты",),
        "group": "materials",
    },
    "meeting": {
        "name": "Встречи клуба",
        "hint": "Встречи «Разговоры с Богом» (телемост)",
        "content_types": ("встреча клуба", "встреча"),
        "group": "materials",
    },
    "product": {
        "name": "О клубе",
        "hint": "Описание продукта, PDF, пояснения",
        "content_types": ("Описание продукта",),
        "group": "library",
    },
    "stories": {
        "name": "Сценарии сториз",
        "hint": "Идеи и сценарии сторис",
        "content_types": ("Сценарии сториз",),
        "group": "library",
    },
    "testimonial": {
        "name": "Отзывы",
        "hint": "Свидетельства и отзывы участников",
        "content_types": ("Отзывы клиентов",),
        "content_categories": ("testimonial",),
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

_GROUP_TITLES = {
    "materials": "Записи и эфиры",
    "library": "Библиотека",
    "audience": "Аудитория",
}

# Совместимость со старыми live:* id в сохранённых чатах.
LIVE_TO_FACET = {
    "chat": "meeting",
    "client": "meeting",
    "expert": "meeting",
    "dialog": "meeting",
    "expert_reply": "meeting",
    "testimonial": "testimonial",
}
LIVE_KEYS = tuple(LIVE_TO_FACET.keys())
LIVE_NAMES = {k: FACET_NAMES.get(LIVE_TO_FACET[k], k) for k in LIVE_KEYS}
LIVE_FILTERS: Dict[str, Dict[str, Any]] = {
    k: dict(FACET_FILTERS.get(LIVE_TO_FACET[k]) or {}) for k in LIVE_KEYS
}
LIVE_ALIASES = {"dialog": "chat", "expert_reply": "expert"}

# Кэш материалов последнего build_tree: mat_id → Material
_MATERIALS: Dict[str, "Material"] = {}


@dataclass
class Material:
    id: str
    facet: str
    source: str
    content_types: Tuple[str, ...] = ()
    content_categories: Tuple[str, ...] = ()
    chunks: int = 0
    chars: int = 0
    date: str = ""
    topic_title: str = ""
    private_link: str = ""
    group_link: str = ""
    product: str = ""
    meeting_id: str = ""

    @property
    def display_name(self) -> str:
        return _pretty_source_name(self.source, self.date)

    @property
    def has_telemost(self) -> bool:
        return bool(self.meeting_id) or "telemost.yandex.ru" in (self.private_link or "")


@dataclass(frozen=True)
class ObjectRef:
    kind: str  # facet | mat | source | lesson | passport | live
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

    def as_material_id(self) -> Optional[str]:
        if self.kind == "mat":
            return self.id
        return None


def material_id(facet: str, source: str) -> str:
    digest = hashlib.sha1(f"{facet}\0{source}".encode("utf-8")).hexdigest()[:16]
    return f"mat:{facet}:{digest}"


def get_material(mat_id: str) -> Optional[Material]:
    return _MATERIALS.get(mat_id)


def ensure_materials(app) -> Dict[str, Material]:
    """Гарантирует заполненный кэш материалов (для pipeline / API)."""
    global _MATERIALS
    if not _MATERIALS:
        _MATERIALS = _scan_rag_materials(app)
    return _MATERIALS


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
    if kind == "mat":
        # value = "<facet>:<hash>"
        facet = value.split(":", 1)[0]
        if facet in FACET_SPECS and ":" in value:
            return ObjectRef("mat", value)
        return None
    if kind == "live":
        value = LIVE_ALIASES.get(value, value)
        value = LIVE_TO_FACET.get(value, value)
        if value in FACET_SPECS:
            return ObjectRef("facet", value)
        return None
    if kind in ("source", "lesson", "passport"):
        return ObjectRef(kind, value)
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
    title = (src.get("title") or src.get("source") or "").strip()
    return (title or "Материал")[:160]


def facet_search_filter(facet_keys: Sequence[str]) -> Optional[Dict[str, Any]]:
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
        clauses.append(parts[0] if len(parts) == 1 else {"$or": parts})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$or": clauses}


def material_search_filter(materials: Sequence[Material]) -> Optional[Dict[str, Any]]:
    """where-extra: выбранные конкретные записи (source + типы)."""
    clauses: List[dict] = []
    for m in materials:
        parts: List[dict] = [{"source": m.source}]
        if m.content_types:
            parts.append({"content_type": {"$in": list(m.content_types)}})
        clauses.append(parts[0] if len(parts) == 1 else {"$and": parts})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$or": clauses}


def _pretty_source_name(source: str, date: str = "") -> str:
    name = unquote((source or "").strip())
    # убрать хвосты ссылок youtube/t.me из имени
    name = re.sub(r"\s*https?://\S+", "", name).strip()
    name = re.sub(r"\s*\[YouTube[^\]]*\]", "", name, flags=re.I).strip()
    name = re.sub(r"^📻\s*", "", name).strip()
    name = re.sub(r"^Полная запись\s*[·.:—-]?\s*", "", name, flags=re.I).strip()
    # molitva_123.mp3 → Молитва 123
    m = re.match(r"^(molitva|efir|pokayanie|qa)_(\d+)\.(mp3|m4a|ogg)$", name, re.I)
    if m:
        kind = {"molitva": "Молитва", "efir": "Эфир", "pokayanie": "Покаяние", "qa": "QA"}.get(
            m.group(1).lower(), m.group(1)
        )
        name = f"{kind} · {m.group(2)}"
    if len(name) > 110:
        name = name[:109].rstrip() + "…"
    d = _short_date(date)
    if d and d not in name:
        return f"{d} · {name}" if name else d
    return name or "Без названия"


def _short_date(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    # 15.07.2026
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", s)
    if m:
        return f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
    # 2026-09-05T...
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(3)}.{m.group(2)}.{m.group(1)}"
    return s[:10]


def _meeting_id_from_link(link: str) -> str:
    m = _MEETING_ID_RE.search(link or "")
    return m.group(1) if m else ""


def _ctype_to_facets(ctype: str, category: str) -> List[str]:
    out: List[str] = []
    for key, spec in FACET_SPECS.items():
        types = set(spec.get("content_types") or ())
        cats = set(spec.get("content_categories") or ())
        if ctype in types or (category and category in cats):
            out.append(key)
    return out


def _scan_rag_materials(app) -> Dict[str, Material]:
    """Сканирует Chroma → материалы по (facet, source)."""
    rag = getattr(app, "rag_stack", None)
    if rag is None:
        return {}
    try:
        coll = rag.vectors.expert_collection
        raw = coll.get(include=["metadatas", "documents"], limit=20_000)
    except Exception as e:
        logger.warning("scan rag materials: %s", e)
        return {}

    allowed = set(product_chroma_names(active_product_id()))
    bucket: Dict[Tuple[str, str], Dict[str, Any]] = {}
    metas = raw.get("metadatas") or []
    docs = raw.get("documents") or []
    for meta, doc in zip(metas, docs):
        m = meta or {}
        prod = str(m.get("product") or "").strip()
        if allowed and prod and prod not in allowed:
            continue
        ctype = str(m.get("content_type") or "").strip()
        cat = str(m.get("content_category") or "").strip()
        source = str(m.get("source") or "").strip()
        if not source:
            continue
        for facet in _ctype_to_facets(ctype, cat):
            key = (facet, source)
            row = bucket.get(key)
            if row is None:
                link = str(m.get("private_source_link") or "")
                row = {
                    "facet": facet,
                    "source": source,
                    "types": set(),
                    "cats": set(),
                    "chunks": 0,
                    "chars": 0,
                    "date": str(m.get("date") or ""),
                    "topic": str(m.get("topic_title") or ""),
                    "private_link": link,
                    "group_link": str(m.get("group_message_link") or ""),
                    "product": prod,
                    "meeting_id": _meeting_id_from_link(link),
                }
                bucket[key] = row
            if ctype:
                row["types"].add(ctype)
            if cat:
                row["cats"].add(cat)
            row["chunks"] += 1
            row["chars"] += len(doc or "")
            if not row["date"] and m.get("date"):
                row["date"] = str(m.get("date"))
            if not row["private_link"] and m.get("private_source_link"):
                row["private_link"] = str(m.get("private_source_link"))
                row["meeting_id"] = _meeting_id_from_link(row["private_link"])

    out: Dict[str, Material] = {}
    for (_facet, _source), row in bucket.items():
        mid = material_id(row["facet"], row["source"])
        out[mid] = Material(
            id=mid,
            facet=row["facet"],
            source=row["source"],
            content_types=tuple(sorted(row["types"])),
            content_categories=tuple(sorted(row["cats"])),
            chunks=int(row["chunks"]),
            chars=int(row["chars"]),
            date=row["date"],
            topic_title=row["topic"],
            private_link=row["private_link"],
            group_link=row["group_link"],
            product=row["product"],
            meeting_id=row["meeting_id"],
        )
    return out


async def _telemost_transcript(stor, meeting_id: str) -> str:
    if not meeting_id or stor is None:
        return ""
    try:
        rows = await stor.list_telemost_pending_by_meeting_id(meeting_id)
    except AttributeError:
        # fallback прямой SQL через pool
        try:
            async with stor.get_connection() as conn:
                rows = await conn.fetch(
                    """
                    SELECT transcript_text, subject, status
                    FROM telemost_mail_pending
                    WHERE classification->'extra'->>'meeting_id' = $1
                    ORDER BY
                      CASE WHEN status = 'indexed' THEN 0 ELSE 1 END,
                      length(transcript_text) DESC
                    LIMIT 3
                    """,
                    meeting_id,
                )
                rows = [dict(r) for r in rows]
        except Exception as e:
            logger.warning("telemost transcript %s: %s", meeting_id, e)
            return ""
    except Exception as e:
        logger.warning("telemost transcript %s: %s", meeting_id, e)
        return ""
    for row in rows or []:
        text = (row.get("transcript_text") or "").strip()
        if text:
            return text
    return ""


def _rag_source_text(app, material: Material, *, max_chars: int = 200_000) -> str:
    rag = getattr(app, "rag_stack", None)
    if rag is None:
        return ""
    try:
        coll = rag.vectors.expert_collection
        where: Dict[str, Any] = {"source": material.source}
        if material.content_types:
            where = {
                "$and": [
                    {"source": material.source},
                    {"content_type": {"$in": list(material.content_types)}},
                ]
            }
        raw = coll.get(where=where, include=["documents", "metadatas"], limit=500)
    except Exception as e:
        logger.warning("rag source text %s: %s", material.id, e)
        return ""
    docs = raw.get("documents") or []
    metas = raw.get("metadatas") or []
    # стабильный порядок по chunk_index
    pairs = list(zip(docs, metas))
    pairs.sort(key=lambda dm: int((dm[1] or {}).get("chunk_index") or 0))
    parts: List[str] = []
    total = 0
    for doc, _meta in pairs:
        text = (doc or "").strip()
        if not text:
            continue
        if total + len(text) > max_chars:
            parts.append(text[: max(0, max_chars - total)].rstrip() + "\n…")
            break
        parts.append(text)
        total += len(text)
    return "\n\n".join(parts).strip()


async def load_material_raw(app, mat_id: str, *, max_chars: int = 200_000) -> Dict[str, Any]:
    """Исходник записи: transcript телемоста (если есть) и/или текст из RAG."""
    global _MATERIALS
    mat = _MATERIALS.get(mat_id)
    if mat is None:
        _MATERIALS = _scan_rag_materials(app)
        mat = _MATERIALS.get(mat_id)
    if mat is None:
        return {"id": mat_id, "found": False, "text": "", "origin": ""}

    stor = getattr(app, "user_storage", None)
    telemost = ""
    if mat.meeting_id:
        telemost = await _telemost_transcript(stor, mat.meeting_id)
    rag_text = _rag_source_text(app, mat, max_chars=max_chars)

    origin = ""
    text = ""
    if telemost:
        origin = "telemost"
        text = telemost
        # если транскрипт короткий — дополним RAG
        if len(text) < 800 and rag_text and rag_text not in text:
            text = (text + "\n\n---\n\n" + rag_text).strip()
            origin = "telemost+rag"
    elif rag_text:
        origin = "rag"
        text = rag_text

    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n…"

    return {
        "id": mat.id,
        "found": True,
        "name": mat.display_name,
        "facet": mat.facet,
        "facet_name": FACET_NAMES.get(mat.facet, mat.facet),
        "source": mat.source,
        "chunks": mat.chunks,
        "chars": len(text),
        "date": mat.date,
        "private_link": mat.private_link,
        "group_link": mat.group_link,
        "meeting_id": mat.meeting_id,
        "has_telemost": mat.has_telemost,
        "origin": origin,
        "text": text,
        "preview": text[:4000],
    }


async def build_tree(app) -> Dict[str, Any]:
    """Тип → список конкретных записей (с возможностью выбрать тип целиком)."""
    global _MATERIALS
    pid = active_product_id()
    materials = _scan_rag_materials(app)
    _MATERIALS = materials

    by_facet: Dict[str, List[Material]] = {k: [] for k in FACET_KEYS}
    for mat in materials.values():
        by_facet.setdefault(mat.facet, []).append(mat)

    for _facet, items in by_facet.items():
        dated = [m for m in items if _short_date(m.date)]
        undated = [m for m in items if not _short_date(m.date)]

        def _stamp(m: Material) -> str:
            parts = _short_date(m.date).split(".")
            return f"{parts[2]}{parts[1]}{parts[0]}" if len(parts) == 3 else ""

        dated.sort(key=lambda m: (_stamp(m), m.chunks), reverse=True)
        undated.sort(key=lambda m: (-m.chunks, m.display_name.casefold()))
        items[:] = dated + undated

    buckets: Dict[str, List[Dict[str, Any]]] = {gid: [] for gid in _GROUP_TITLES}
    for key, spec in FACET_SPECS.items():
        kids = by_facet.get(key) or []
        if not kids:
            continue
        total_chunks = sum(m.chunks for m in kids)
        children = [
            {
                "id": m.id,
                "kind": "mat",
                "name": m.display_name,
                "meta": _child_meta(m),
                "status": "ready",
                "selectable": True,
                "facet": key,
                "has_source": True,
                "has_telemost": m.has_telemost,
                "parent": f"facet:{key}",
            }
            for m in kids
        ]
        node = {
            "id": f"facet:{key}",
            "kind": "facet",
            "name": spec["name"],
            "meta": (
                f"{len(kids)} записей · {total_chunks} фрагментов · {spec['hint']}"
            ),
            "status": "ready",
            "selectable": True,
            "facet": key,
            "children": children,
        }
        gid = str(spec.get("group") or "materials")
        buckets.setdefault(gid, []).append(node)

    groups: List[Dict[str, Any]] = []
    for gid, title in _GROUP_TITLES.items():
        items = buckets.get(gid) or []
        if not items:
            continue
        hint = {
            "materials": "Тип → конкретная запись. Можно выбрать весь тип или отдельные эфиры/молитвы.",
            "library": "Справочные материалы и сценарии.",
            "audience": "Голос участников.",
        }.get(gid, "")
        groups.append({"id": gid, "title": title, "hint": hint, "items": items})

    return {
        "product": {"id": pid, "name": product_display_name(pid)},
        "groups": groups,
    }


def _child_meta(m: Material) -> str:
    bits = [f"{m.chunks} фрагм."]
    if m.has_telemost:
        bits.append("исходник телемоста")
    elif m.chars:
        bits.append("текст в RAG")
    if m.private_link and "t.me" in m.private_link:
        bits.append("telegram")
    return " · ".join(bits)


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
