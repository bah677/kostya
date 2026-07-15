"""Классификация ссылок RAG для member-агента клуба."""

from __future__ import annotations

import re
from typing import Iterable, Optional

# Внутренний id супергруппы клуба «Любящие Бога» (-1003882558802).
CLUB_GROUP_INTERNAL_ID = "3882558802"

_TME_URL_RE = re.compile(r"https?://t\.me/[^\s\]<>\")']+", re.IGNORECASE)
_BARE_TME_RE = re.compile(r"(?<![/\w])t\.me/[^\s\]<>\")']+", re.IGNORECASE)
_PUBLIC_LABEL_RE = re.compile(
    r"публичная ссылка:\s*(https?://\S+)",
    re.IGNORECASE,
)
_LEGACY_PUBLIC_LABEL_RE = re.compile(
    r"ссылка:\s*(https?://\S+)",
    re.IGNORECASE,
)


def normalize_url(url: str) -> str:
    u = (url or "").strip().rstrip(".,;:)")
    while u.endswith("]"):
        u = u[:-1]
    if u and not u.startswith("http"):
        u = f"https://{u}"
    return u


def is_youtube_url(url: str) -> bool:
    low = (url or "").lower()
    return "youtube.com" in low or "youtu.be" in low


def is_club_group_message_link(url: str) -> bool:
    u = normalize_url(url)
    return f"/c/{CLUB_GROUP_INTERNAL_ID}/" in u


def classify_source_link_visibility(url: str) -> Optional[str]:
    """
    public — можно показывать участникам клуба;
    private — только для обучения/внутреннего RAG, не в ответах.
    """
    u = normalize_url(url)
    if not u:
        return None
    if is_youtube_url(u):
        return "public"
    if is_club_group_message_link(u):
        return "public"
    if "t.me" in u.lower():
        return "private"
    return "private"


def is_public_member_link(url: str) -> bool:
    return classify_source_link_visibility(url) == "public"


def pick_link_from_metadata(meta: dict) -> str:
    for key in ("public_source_link", "private_source_link", "group_message_link"):
        val = str(meta.get(key) or "").strip()
        if val:
            return val
    return ""

def apply_classified_link_metadata(meta: dict) -> tuple[dict, bool]:
    """Перенести legacy/смешанные поля в public/private по правилам клуба."""
    url = pick_link_from_metadata(meta)
    out = dict(meta)
    changed = bool(url) or bool((out.get("group_message_link") or "").strip())
    out.pop("group_message_link", None)
    out.pop("public_source_link", None)
    out.pop("private_source_link", None)
    if not url:
        # Chroma merge: явно гасим legacy-ключ
        out["group_message_link"] = ""
        return out, changed
    vis = classify_source_link_visibility(url)
    if vis == "public":
        out["public_source_link"] = url[:500]
    else:
        out["private_source_link"] = url[:500]
    out["group_message_link"] = ""
    return out, True


def extract_public_links_from_text(*parts: Optional[str]) -> list[str]:
    """Ссылки, которые member-агенту разрешено вставлять в ответ."""
    found: set[str] = set()
    for part in parts:
        if not part:
            continue
        for pattern in (_PUBLIC_LABEL_RE, _LEGACY_PUBLIC_LABEL_RE):
            for m in pattern.findall(part):
                url = normalize_url(m)
                if is_public_member_link(url):
                    found.add(url)
    return sorted(found)


def extract_any_tme_links(text: str) -> list[str]:
    if not text:
        return []
    out: set[str] = set()
    for m in _TME_URL_RE.findall(text):
        out.add(normalize_url(m))
    for m in _BARE_TME_RE.findall(text):
        out.add(normalize_url(m))
    return sorted(out)


def metadata_link_fields_for_scan(meta: dict) -> Iterable[str]:
    m, _ = apply_classified_link_metadata(dict(meta or {}))
    for key in ("public_source_link", "private_source_link"):
        val = str(m.get(key) or "").strip()
        if val:
            yield val
