"""Скиллы видов контента (обычно markdown): правила генерации, не этапы прогрева."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from course.formats import FORMATS, format_prompt_block
from course.models import FormatSpec

logger = logging.getLogger(__name__)

FORMAT_SKILLS_KEY = "format_skills"
_SKILLS_DIR = Path(__file__).resolve().parent / "skills"
_MAX_SKILL_CHARS = 100_000


def _read_skill_md(format_id: str) -> str:
    path = _SKILLS_DIR / f"{format_id}.md"
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception as e:
        logger.warning("skill md %s: %s", path, e)
        return ""


def default_skill_text(spec: FormatSpec) -> str:
    """Дефолт: course/skills/{id}.md, иначе краткое описание из FormatSpec."""
    from_file = _read_skill_md(spec.id)
    if from_file:
        return from_file
    return format_prompt_block(spec).strip()


def decode_format_skills(raw: Any) -> Dict[str, Dict[str, str]]:
    if isinstance(raw, (bytes, memoryview)):
        raw = bytes(raw).decode("utf-8")
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return {}
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("format_skills: value is not JSON object")
            return {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for fid, row in raw.items():
        if str(fid) not in FORMATS:
            continue
        if isinstance(row, str):
            skill = row.strip()
            title = ""
        elif isinstance(row, dict):
            skill = str(row.get("skill") or "").strip()
            title = str(row.get("title") or "").strip()
        else:
            continue
        if skill or title:
            out[str(fid)] = {
                "skill": skill[:_MAX_SKILL_CHARS],
                "title": title[:200],
            }
    return out


def resolved_formats(
    overrides: Optional[Dict[str, Dict[str, str]]] = None,
) -> List[Dict[str, Any]]:
    ov = overrides or {}
    rows: List[Dict[str, Any]] = []
    for fid, spec in FORMATS.items():
        patch = ov.get(fid) or {}
        custom = str(patch.get("skill") or "").strip()
        skill = custom or default_skill_text(spec)
        title = str(patch.get("title") or "").strip() or spec.title
        rows.append(
            {
                "id": fid,
                "title": title,
                "platform": spec.platform,
                "skill": skill,
                "default_skill": default_skill_text(spec),
                "customized": bool(custom),
            }
        )
    return rows


async def load_format_skills(stor) -> Dict[str, Dict[str, str]]:
    from course.products import active_product_id

    raw = await stor.get_content_setting(active_product_id(), FORMAT_SKILLS_KEY)
    return decode_format_skills(raw)


async def load_format_skill(stor, format_id: str) -> str:
    fid = (format_id or "").strip()
    spec = FORMATS.get(fid)
    if not spec:
        return ""
    ov = await load_format_skills(stor)
    skill = str((ov.get(fid) or {}).get("skill") or "").strip()
    return skill or default_skill_text(spec)


async def save_format_skill(
    stor,
    format_id: str,
    *,
    skill: str,
    title: str = "",
    user_id: int = 0,
    reset: bool = False,
) -> Dict[str, Any]:
    from course.products import active_product_id

    _ = user_id
    fid = (format_id or "").strip()
    spec = FORMATS.get(fid)
    if not spec:
        raise ValueError(f"unknown format: {format_id}")
    current = await load_format_skills(stor)
    text = (skill or "").strip()
    ttl = (title or "").strip()
    default = default_skill_text(spec)

    if reset or not text or (text == default and not (ttl and ttl != spec.title)):
        current.pop(fid, None)
    else:
        row: Dict[str, str] = {"skill": text[:_MAX_SKILL_CHARS]}
        if ttl and ttl != spec.title:
            row["title"] = ttl[:200]
        current[fid] = row

    pid = active_product_id()
    await stor.set_content_setting(pid, FORMAT_SKILLS_KEY, current)
    # Перечитать из БД — чтобы поймать поломку записи сразу.
    stored = decode_format_skills(await stor.get_content_setting(pid, FORMAT_SKILLS_KEY))
    if text and text != default and fid not in stored:
        raise RuntimeError("скилл не записался в content_settings")
    if text and text != default:
        got = str((stored.get(fid) or {}).get("skill") or "")
        if got != text[:_MAX_SKILL_CHARS]:
            raise RuntimeError("скилл записался обрезанным или повреждённым")
    return next(r for r in resolved_formats(stored) if r["id"] == fid)


async def list_format_skills(stor) -> List[Dict[str, Any]]:
    return resolved_formats(await load_format_skills(stor))
