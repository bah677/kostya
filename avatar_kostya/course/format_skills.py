"""Скиллы видов контента: правила генерации живут здесь, не в этапах прогрева."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from course.formats import FORMATS, format_prompt_block
from course.models import FormatSpec

FORMAT_SKILLS_KEY = "format_skills"

# Базовые скиллы: то, что уходит в WRITE вместе с паспортами.
# Этап прогрева — отдельно в контексте; скилл говорит, как к нему относиться.
_STORIES_SKILL_EXTRA = """
Раскадровка на день, не один абзац: 8–12 кадров.
На кадр: текст НА ЭКРАНЕ (до 8–12 слов), что сказать голосом (1–2 фразы),
стикер если есть, роль кадра (хук/боль/метод/интерактив/доказ/CTA).
Этап прогрева задан в контексте задачи — подстраивай жёсткость оффера и CTA под него:
на прогреве почти без продажи; на предзапуске собирай интерес; в окне продаж — оффер и куда писать;
после закрытия — тепло и процесс у тех, кто уже внутри.
Не обещай цен, дат и результатов вне паспорта запуска.
""".strip()


def default_skill_text(spec: FormatSpec) -> str:
    parts = [format_prompt_block(spec)]
    if spec.id == "stories":
        parts.append(_STORIES_SKILL_EXTRA)
    return "\n\n".join(p for p in parts if (p or "").strip()).strip()


def decode_format_skills(raw: Any) -> Dict[str, Dict[str, str]]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for fid, row in raw.items():
        if fid not in FORMATS or not isinstance(row, dict):
            continue
        skill = str(row.get("skill") or "").strip()
        title = str(row.get("title") or "").strip()
        if skill or title:
            out[str(fid)] = {"skill": skill[:20000], "title": title[:200]}
    return out


def resolved_formats(
    overrides: Optional[Dict[str, Dict[str, str]]] = None,
) -> List[Dict[str, Any]]:
    ov = overrides or {}
    rows: List[Dict[str, Any]] = []
    for fid, spec in FORMATS.items():
        patch = ov.get(fid) or {}
        skill = str(patch.get("skill") or "").strip() or default_skill_text(spec)
        title = str(patch.get("title") or "").strip() or spec.title
        rows.append(
            {
                "id": fid,
                "title": title,
                "platform": spec.platform,
                "skill": skill,
                "default_skill": default_skill_text(spec),
                "customized": bool(str(patch.get("skill") or "").strip()),
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
    # Пустой skill или равный дефолту — сбрасываем кастом.
    if not text or text == default_skill_text(spec):
        current.pop(fid, None)
    else:
        row: Dict[str, str] = {"skill": text[:20000]}
        if ttl and ttl != spec.title:
            row["title"] = ttl[:200]
        current[fid] = row
    await stor.set_content_setting(active_product_id(), FORMAT_SKILLS_KEY, current)
    return next(r for r in resolved_formats(current) if r["id"] == fid)


async def list_format_skills(stor) -> List[Dict[str, Any]]:
    return resolved_formats(await load_format_skills(stor))
