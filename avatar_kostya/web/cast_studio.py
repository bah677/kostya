"""Герои и сцены для киностудии: ДНК, список кадров и галерея банка.

Описание героев — это не настройка, а то, что держит узнаваемость: один и
тот же абзац текста уходит в каждый кадр, и пока он неизменен, героиня
остаётся собой. Поэтому правки показываются рядом с исходным текстом из
кода и легко откатываются.

Галерея отдаёт не сами кадры, а превью: оригинал весит около двух с
половиной мегабайт.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from youtube_prayer import cast, cast_bank, cast_store

logger = logging.getLogger(__name__)

_WHO_LABEL = {"woman": "героиня", "jesus": "Иисус", "both": "вдвоём"}


def _field(name: str, default: str, current: str) -> Dict[str, Any]:
    over = cast_store.text(name) or ""
    return {
        "key": name,
        "value": current,
        "default": default,
        "overridden": bool(over) and over != default,
    }


def _frames(scene: cast.Scene, heroine: str) -> List[Dict[str, Any]]:
    out = []
    folder = cast.bank_folder(scene.who, heroine)
    for path in cast_bank.frames_for_scene(scene, heroine):
        thumb = cast_bank.thumb_file(path)
        out.append(
            {
                "name": path.name,
                "url": f"/api/cast/frame/{folder}/{path.name}",
                "thumb": f"/api/cast/frame/{folder}/{path.name}?thumb=1" if thumb else "",
                "kb": round(path.stat().st_size / 1024),
            }
        )
    return out


def _scene_row(scene: cast.Scene) -> Dict[str, Any]:
    # Кадры с Иисусом общие: он один на обеих героинь, и рисовать их
    # дважды значило бы платить за одно и то же.
    shared = scene.who == "jesus"
    frames: Dict[str, Any] = {}
    nexts: Dict[str, int] = {}
    for h in cast.HEROINES:
        frames[h.key] = _frames(scene, h.key)
        nexts[h.key] = cast_bank.next_variant(scene, h.key)
    return {
        "key": scene.key,
        "who": scene.who,
        "who_label": _WHO_LABEL.get(scene.who, scene.who),
        "shared": shared,
        "shot": scene.shot,
        "prompt": scene.prompt,
        "topics": list(scene.topics),
        "frames": frames,
        "next_variant": nexts,
    }


def describe() -> Dict[str, Any]:
    rows = [_scene_row(s) for s in cast.scenes()]
    heroines = []
    for h in cast.HEROINES:
        heroines.append(
            {
                "key": h.key,
                "label": h.label,
                "note": h.note,
                "store_key": h.store_key,
                "field": _field(h.store_key, h.default_prompt, cast.woman(h.key)),
                "frames": sum(len(r["frames"][h.key]) for r in rows),
            }
        )
    return {
        "style": _field("style", cast.STYLE, cast.style()),
        "jesus": _field("jesus", cast.JESUS, cast.jesus()),
        "heroines": heroines,
        "default_heroine": cast.DEFAULT_HEROINE,
        "scenes": rows,
        "scenes_overridden": cast_store.scenes_raw() is not None,
        "who_values": [{"key": k, "label": v} for k, v in _WHO_LABEL.items()],
        "bank_dir": str(cast_bank.bank_dir()),
        "frames_total": sum(
            len(r["frames"][h["key"]]) for r in rows for h in heroines if not r["shared"]
        )
        + sum(len(r["frames"][cast.DEFAULT_HEROINE]) for r in rows if r["shared"]),
        "model": cast_bank.model_name(),
        "price": cast_bank.price_per_frame(),
    }


def _same_scenes(rows: List[Dict[str, Any]]) -> bool:
    """Совпадает ли присланный список с набором из кода."""
    base = cast.DEFAULT_SCENES
    if len(rows) != len(base):
        return False
    for got, want in zip(rows, base):
        if (
            str(got.get("key") or "").strip() != want.key
            or str(got.get("who") or "").strip() != want.who
            or str(got.get("shot") or "").strip() != want.shot
            or str(got.get("prompt") or "").strip() != want.prompt
            or [str(t).strip() for t in (got.get("topics") or [])] != list(want.topics)
        ):
            return False
    return True


def save(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Пишет только то, что отличается от кода: файл остаётся читаемым."""
    data: Dict[str, Any] = {}
    texts = [("style", cast.STYLE), ("jesus", cast.JESUS)]
    texts += [(h.store_key, h.default_prompt) for h in cast.HEROINES]
    for name, default in texts:
        value = str((payload or {}).get(name) or "").strip()
        if value and value != default.strip():
            data[name] = value

    rows = (payload or {}).get("scenes")
    if isinstance(rows, list) and rows and not _same_scenes(rows):
        data["scenes"] = rows

    cast_store.save(data)
    return describe()


async def generate(key: str, heroine: str = "") -> Dict[str, Any]:
    name, what = await cast_bank.generate_scene(key, heroine=heroine)
    return {
        "ok": True,
        "frame": name,
        "status": what,
        "heroine": cast.heroine(heroine).label,
    }
