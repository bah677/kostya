"""Метаданные YouTube Shorts (#Shorts в названии)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence
from zoneinfo import ZoneInfo

from youtube_prayer.metadata import VideoMetadata, _clamp_title, _normalize_hashtags

logger = logging.getLogger(__name__)
_MSK = ZoneInfo("Europe/Moscow")


def _ensure_shorts_title(title: str) -> str:
    t = re.sub(r"\s+", " ", (title or "").strip())
    if "#shorts" in t.casefold():
        return _clamp_title(t, max_len=95)
    base = _clamp_title(t, max_len=88)
    return _clamp_title(f"{base} #Shorts", max_len=95)


def _fallback_short_metadata(*, trend: str, brief: str) -> VideoMetadata:
    title = _ensure_shorts_title(f"Молитва: {trend[:40]}")
    desc = (
        f"Короткая молитва на 1–2 минуты: {brief}\n\n"
        "Спокойный голос, можно слушать с закрытыми глазами.\n"
        "Лайк, подписка и комментарий «Аминь», если молитва откликнулась.\n\n"
        "---\n"
        f"Keywords: молитва, shorts, христианская молитва, {trend}, вера, утешение"
    )
    tags = ["#Shorts", "#молитва", "#вера", "#христианство", "#утешение"]
    return VideoMetadata(
        title=title,
        thumbnail_title=title[:42],
        description=desc,
        hashtags=tags,
    )


async def generate_short_metadata(
    *,
    trend: str,
    brief: str,
    complete_fn,
    trend_pool: Optional[Sequence[str]] = None,
    work_dir: Optional[Path] = None,
) -> VideoMetadata:
    today = datetime.now(_MSK).strftime("%d.%m.%Y")
    pool = [t for t in (trend_pool or []) if t and t != trend][:10]
    pool_txt = "\n".join(f"- {t}" for t in pool) if pool else "(нет доп. трендов)"
    system = (
        "Ты пишешь метаданные для YouTube Shorts — коротких вертикальных молитв (1–2 мин). "
        "Ответ СТРОГО JSON:\n"
        '{"title":"...","thumbnail_title":"...","description":"...","hashtags":["#Shorts",...]}\n\n'
        "title: 40–85 символов, цепляющий, в конце обязательно #Shorts (если не влезает — сократи текст).\n"
        "thumbnail_title: 3–6 слов для обложки.\n"
        "description: 2–4 коротких абзаца + CTA; в конце Keywords через ---.\n"
        "hashtags: 6–10 тегов, первый #Shorts.\n"
    )
    user = (
        f"Дата (МСК): {today}\n"
        f"Тренд: {trend}\n"
        f"Бриф: {brief}\n"
        f"Другие тренды:\n{pool_txt}"
    )
    raw = await complete_fn(system, user)
    try:
        text = (raw or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        data = json.loads(text)
        title = _ensure_shorts_title(str(data.get("title") or ""))
        thumb = str(data.get("thumbnail_title") or title).strip()[:42]
        description = str(data.get("description") or "").strip()
        hashtags = _normalize_hashtags(data.get("hashtags") or [], lang="ru")
        if "#Shorts".casefold() not in {h.casefold() for h in hashtags}:
            hashtags = ["#Shorts"] + hashtags
        if len(title) < 8 or len(description) < 40:
            raise ValueError("metadata too short")
        meta = VideoMetadata(
            title=title,
            thumbnail_title=thumb,
            description=description,
            hashtags=hashtags[:12],
        )
    except Exception as e:
        logger.warning("short metadata LLM failed trend=%r: %s", trend, e)
        meta = _fallback_short_metadata(trend=trend, brief=brief)

    if work_dir is not None:
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "meta.json").write_text(
            json.dumps(
                {
                    "title": meta.title,
                    "thumbnail_title": meta.thumbnail_title,
                    "description": meta.description,
                    "hashtags": meta.hashtags,
                    "description_full": meta.description_with_hashtags,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return meta
