"""YouTube-метаданные: триггерное название, SEO-описание, хэштеги."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_MSK = ZoneInfo("Europe/Moscow")

# Семантические блоки описания (порядок важен для YouTube / поиска).
_DESC_BLOCK_HINTS_RU = """
Структура description (сохрани блоки, можно чуть менять формулировки):
1) HOOK — первая строка с главным ключом и болью (до ~120 символов, без эмодзи в начале).
2) О ВИДЕО — 2–3 предложения: что за молитва, для кого, какой результат души.
3) СЕЙЧАС АКТУАЛЬНО — 1–2 предложения, мягко связать с трендом/запросом (без новостного хайпа).
4) КАК СЛУШАТЬ — коротко: спокойный голос, можно с закрытыми глазами, в своём темпе.
5) ПРИЗЫВ — лайк, подписка, комментарий «Аминь», поделиться с тем, кому нужно.
6) РАЗДЕЛИТЕЛЬ --- и строка Keywords: 8–12 поисковых фраз через запятую
   (включи 2–4 из списка трендов, если уместно; остальное — молитва, вера, тревога и т.д.).
""".strip()

_DESC_BLOCK_HINTS_EN = """
Description structure (keep blocks, wording may vary):
1) HOOK — first line with primary keyword and pain point (~120 chars, no leading emoji).
2) ABOUT — 2–3 sentences: what this prayer is, who it's for, spiritual comfort.
3) WHY NOW — 1–2 sentences tying gently to the trend/search (no news hype).
4) HOW TO LISTEN — brief: calm voiceover, eyes closed, your own pace.
5) CTA — like, subscribe, comment Amen, share with someone who needs it.
6) Separator --- then line Keywords: 8–12 search phrases comma-separated
   (include 2–4 from trends list when natural; rest: prayer, faith, anxiety, etc.).
""".strip()


@dataclass(frozen=True)
class VideoMetadata:
    title: str
    thumbnail_title: str
    description: str
    hashtags: List[str]
    hook_question: str = ""

    @property
    def description_with_hashtags(self) -> str:
        tags = " ".join(self.hashtags)
        body = (self.description or "").rstrip()
        if not tags:
            return body
        if tags in body:
            return body
        return f"{body}\n\n{tags}".strip()


def _parse_json_obj(raw: str) -> dict:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{[\s\S]+\}", text)
        if m:
            return json.loads(m.group(0))
        raise


def _clamp_title(title: str, *, max_len: int = 95) -> str:
    t = re.sub(r"\s+", " ", (title or "").strip()).strip(" «»\"'")
    if len(t) <= max_len:
        return t
    cut = t[: max_len - 1].rsplit(" ", 1)[0]
    return (cut or t[: max_len - 1]).rstrip(".,;:!?") + "…"


def _clamp_thumbnail_title(title: str, *, max_len: int = 42) -> str:
    """Короткий кликбейт для обложки: 3–8 слов, читается на миниатюре."""
    t = re.sub(r"\s+", " ", (title or "").strip()).strip(" «»\"'")
    if len(t) <= max_len:
        return t
    cut = t[: max_len - 1].rsplit(" ", 1)[0]
    return (cut or t[: max_len - 1]).rstrip(".,;:!?") + "…"


def _clamp_hook_question(q: str, *, max_len: int = 56) -> str:
    t = re.sub(r"\s+", " ", (q or "").strip()).strip(" «»\"'")
    if not t:
        return ""
    if not t.endswith(("?", "؟")):
        t = t.rstrip(".!") + "?"
    if len(t) <= max_len:
        return t
    cut = t[: max_len - 1].rsplit(" ", 1)[0]
    return (cut or t[: max_len - 1]).rstrip(".,;:!") + "?"


def _hook_from_thumb(thumb: str, *, lang: str) -> str:
    base = (thumb or "").strip().rstrip("?!.")
    if not base:
        return "Тебе это знакомо?" if lang != "en" else "Does this feel familiar?"
    if lang == "en":
        return _clamp_hook_question(f"Is this your story — {base}?")
    return _clamp_hook_question(f"Это про тебя — {base}?")


def _thumbnail_from_title(title: str, *, lang: str) -> str:
    """Запасной короткий заголовок из полного названия."""
    words = (title or "").split()
    if len(words) <= 8 and len(title) <= 42:
        return title
    short = " ".join(words[:6])
    return _clamp_thumbnail_title(short)


def _normalize_hashtags(raw: Sequence[str], *, lang: str) -> List[str]:
    out: List[str] = []
    for item in raw or []:
        tag = str(item or "").strip()
        if not tag:
            continue
        tag = tag.replace(" ", "")
        if not tag.startswith("#"):
            tag = "#" + tag.lstrip("#")
        tag = re.sub(r"[^\w#а-яёА-ЯЁ]", "", tag, flags=re.I)
        if len(tag) < 2:
            continue
        if tag.lower() not in {t.lower() for t in out}:
            out.append(tag)
    if len(out) < 3:
        defaults = (
            ["#молитва", "#вера", "#христианство", "#утешение", "#Бог"]
            if lang != "en"
            else ["#prayer", "#faith", "#Christianity", "#comfort", "#God"]
        )
        for d in defaults:
            if d.lower() not in {t.lower() for t in out}:
                out.append(d)
            if len(out) >= 8:
                break
    return out[:15]


def _fallback_metadata(*, trend: str, brief: str, lang: str) -> VideoMetadata:
    lang = (lang or "ru").lower()
    if lang == "en":
        title = f"A Prayer When {trend[:40]} Weighs on Your Heart"
        thumb = "When Your Heart Can't Take It"
        desc = (
            f"Personal Christian prayer for {brief}\n\n"
            "In this video — a calm spoken prayer to Heavenly Father: "
            "honest words for your situation, Scripture, and peace for your soul.\n\n"
            "Listen in a quiet moment. If this helped you, like, subscribe, "
            "and comment Amen.\n\n"
            "---\n"
            f"Keywords: prayer, Christian prayer, {trend}, faith, comfort, anxiety, hope, Jesus"
        )
        tags = ["#prayer", "#ChristianPrayer", "#faith", "#comfort", "#God"]
    else:
        title = f"Молитва, когда {trend[:35]} — обратись к Богу"
        thumb = "Когда нет сил — молись"
        desc = (
            f"Личная христианская молитва: {brief}\n\n"
            "В этом видео — спокойная молитва Небесному Отцу: "
            "честные слова о вашей ситуации, Писание и утешение для души.\n\n"
            "Слушайте в тишине. Если молитва откликнулась — лайк, подписка "
            "и комментарий «Аминь».\n\n"
            "---\n"
            f"Keywords: молитва, христианская молитва, {trend}, вера, утешение, тревога, надежда"
        )
        tags = ["#молитва", "#христианство", "#вера", "#утешение", "#Бог"]
    return VideoMetadata(
        title=_clamp_title(title),
        thumbnail_title=_clamp_thumbnail_title(thumb),
        description=desc,
        hashtags=tags,
        hook_question=_hook_from_thumb(thumb, lang=lang),
    )


async def generate_video_metadata(
    *,
    trend: str,
    brief: str,
    lang: str,
    complete_fn,
    trend_pool: Optional[Sequence[str]] = None,
    work_dir: Optional[Path] = None,
) -> VideoMetadata:
    """Триггерное название + SEO-описание + хэштеги (с учётом пула трендов)."""
    lang = (lang or "ru").lower()
    today = datetime.now(_MSK).strftime("%d.%m.%Y")
    pool = [t for t in (trend_pool or []) if t and t != trend][:10]
    pool_txt = "\n".join(f"- {t}" for t in pool) if pool else "(нет доп. трендов)"

    if lang == "en":
        system = (
            "You write YouTube metadata for Christian prayer videos (USA audience). "
            "Return STRICT JSON only:\n"
            '{"title":"...","thumbnail_title":"...","hook_question":"...","description":"...","hashtags":["#Prayer",...]}\n\n'
            "title: 50–90 chars, SEO-friendly, emotional, reverent, timely, matches trend pain.\n"
            "thumbnail_title: 3–8 words ONLY, max ~40 chars — punchy clickbait for the thumbnail "
            "(e.g. «When Anxiety Won't Let Go», «God Hears You Tonight»). "
            "Must hit the pain harder than title; no emoji, no ALL CAPS, no blasphemy.\n"
            "hook_question: ONE short question for the first 2 seconds of the video "
            "(max ~50 chars, ends with ?), personal and urgent — e.g. «Can't sleep again?».\n"
            "description: follow the semantic blocks below; plain text, line breaks OK.\n"
            "hashtags: 8–12 tags, mix broad (#Prayer) and niche (#AnxietyPrayer).\n\n"
            + _DESC_BLOCK_HINTS_EN
        )
        user = (
            f"Date (MSK): {today}\n"
            f"Main trend: {trend}\n"
            f"Prayer brief: {brief}\n"
            f"Other Google Trends (use 2–4 in Keywords if natural):\n{pool_txt}"
        )
    else:
        system = (
            "Ты пишешь метаданные YouTube для канала христианских молитв (RU). "
            "Ответ СТРОГО JSON:\n"
            '{"title":"...","thumbnail_title":"...","hook_question":"...","description":"...","hashtags":["#молитва",...]}\n\n'
            "title: 50–90 символов, SEO + эмоция, актуально, без кощунства и эмодзи.\n"
            "thumbnail_title: только 3–8 слов, до ~40 символов — жёсткий кликбейт для обложки "
            "(пример: «Когда тревога не отпускает», «Бог слышит тебя сейчас»). "
            "Бьёт в боль сильнее, чем title; без КАПСА и кощунства.\n"
            "hook_question: ОДИН короткий вопрос на первые 2 секунды ролика "
            "(до ~50 символов, заканчивается ?), личный и острый — напр. «Снова не можешь уснуть?».\n"
            "description: по семантическим блокам ниже; обычный текст, переносы строк.\n"
            "hashtags: 8–12 тегов, микс широких (#молитва) и узких (#молитваоттревоги).\n\n"
            + _DESC_BLOCK_HINTS_RU
        )
        user = (
            f"Дата (МСК): {today}\n"
            f"Главный тренд: {trend}\n"
            f"Бриф молитвы: {brief}\n"
            f"Другие тренды Google (2–4 вплети в Keywords, если уместно):\n{pool_txt}"
        )

    raw = await complete_fn(system, user)
    try:
        data = _parse_json_obj(raw or "")
        title = _clamp_title(str(data.get("title") or ""))
        thumb_raw = str(data.get("thumbnail_title") or "").strip()
        thumbnail_title = _clamp_thumbnail_title(thumb_raw) if thumb_raw else ""
        if len(thumbnail_title) < 4:
            thumbnail_title = _thumbnail_from_title(title, lang=lang)
        description = str(data.get("description") or "").strip()
        hashtags = _normalize_hashtags(data.get("hashtags") or [], lang=lang)
        hook_raw = str(data.get("hook_question") or "").strip()
        hook_question = _clamp_hook_question(hook_raw) if hook_raw else ""
        if len(hook_question) < 6:
            hook_question = _hook_from_thumb(thumbnail_title, lang=lang)
        if len(title) < 8 or len(description) < 80:
            raise ValueError("metadata too short")
        meta = VideoMetadata(
            title=title,
            thumbnail_title=thumbnail_title,
            description=description,
            hashtags=hashtags,
            hook_question=hook_question,
        )
    except Exception as e:
        logger.warning("metadata LLM parse failed trend=%r: %s", trend, e)
        meta = _fallback_metadata(trend=trend, brief=brief, lang=lang)

    if work_dir is not None:
        work_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "title": meta.title,
            "thumbnail_title": meta.thumbnail_title,
            "hook_question": meta.hook_question,
            "description": meta.description,
            "hashtags": meta.hashtags,
            "description_full": meta.description_with_hashtags,
        }
        (work_dir / "meta.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (work_dir / "description.txt").write_text(
            meta.description_with_hashtags + "\n",
            encoding="utf-8",
        )
        (work_dir / "title.txt").write_text(meta.title + "\n", encoding="utf-8")
        (work_dir / "thumbnail_title.txt").write_text(
            meta.thumbnail_title + "\n", encoding="utf-8"
        )
        if meta.hook_question:
            (work_dir / "hook_question.txt").write_text(
                meta.hook_question + "\n", encoding="utf-8"
            )

    logger.info(
        "metadata ok trend=%r title=%r thumb=%r tags=%s",
        trend,
        meta.title,
        meta.thumbnail_title,
        len(meta.hashtags),
    )
    return meta
