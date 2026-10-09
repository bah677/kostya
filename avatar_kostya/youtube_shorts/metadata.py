"""Метаданные YouTube Shorts (#Shorts в названии)."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, List, Optional, Sequence
from zoneinfo import ZoneInfo

from youtube_prayer.langs import destination_for, normalize_lang, profile
from youtube_prayer.metadata import VideoMetadata, _clamp_title, _normalize_hashtags

logger = logging.getLogger(__name__)
_MSK = ZoneInfo("Europe/Moscow")

CompleteFn = Callable[[str, str], Awaitable[Optional[str]]]

# Повелительное после тире, обрыв «Молитва, когда X — …»
_BAD_TITLE_RES = (
    re.compile(r"—\s*(обратись|помолись|прочитай|слушай|скажи|открой|вспомни)\b", re.I),
    re.compile(r"^\s*молитва,\s*когда\s+[^—]+—\s*\S", re.I),
    re.compile(r"\?\s*#?\s*shorts\s*$", re.I),
)


def _ensure_shorts_title(title: str) -> str:
    t = re.sub(r"\s+", " ", (title or "").strip())
    if "#shorts" in t.casefold():
        return _clamp_title(t, max_len=95)
    base = _clamp_title(t, max_len=88)
    return _clamp_title(f"{base} #Shorts", max_len=95)


def _strip_shorts_suffix(title: str) -> str:
    t = re.sub(r"\s+", " ", (title or "").strip())
    return re.sub(r"\s*#shorts\s*$", "", t, flags=re.I).strip()


def _title_quality_ok(title: str) -> bool:
    core = _strip_shorts_suffix(title)
    if len(core) < 12 or len(core) > 88:
        return False
    if core.count("—") > 1:
        return False
    if core.endswith(("—", ",", ":", ";", "…")):
        return False
    if not re.search(r"[а-яёa-z0-9]", core, flags=re.I):
        return False
    for rx in _BAD_TITLE_RES:
        if rx.search(core):
            return False
    # После тире — не голый глагол в повелительном наклонении.
    m = re.search(r"—\s*(\S+)", core)
    if m and re.match(
        r"^(обратись|помолись|прочитай|слушай|скажи|открой|вспомни|приди|доверься)\b",
        m.group(1),
        re.I,
    ):
        return False
    return True


def _viral_fallback_title(trend: str, *, lang: str = "ru") -> str:
    t = re.sub(r"\s+", " ", (trend or "").strip(" .,—–-"))
    code = normalize_lang(lang)
    if code == "es":
        t = re.sub(r"^oraci[oó]n\s+(de|por|para|:)\s*", "", t, flags=re.I).strip()
        if not t:
            t = "el alma pesada"
        title = (
            f"{t} — una oración que sostiene"
            if "?" in t
            else f"Cuando {t} — una oración que sostiene"
        )
        return _ensure_shorts_title(title)
    if code == "en":
        t = re.sub(r"^prayer\s+(for|about|of|:)\s*", "", t, flags=re.I).strip()
        if not t:
            t = "your heart feels heavy"
        title = (
            f"{t} — a prayer that holds you"
            if "?" in t
            else f"When {t} — a prayer that holds you"
        )
        return _ensure_shorts_title(title)
    t = re.sub(r"^молитва\s+(о|об|про|за|:)\s*", "", t, flags=re.I).strip()
    if not t:
        t = "тяжело на душе"
    low = t.lower()
    if "?" in t:
        title = f"{t} — молитва, которая поддержит"
    elif low.startswith("когда "):
        title = f"{t[0].upper()}{t[1:]} — молитва, которая поддержит"
    else:
        title = f"Когда {t} — молитва, которая поддержит"
    return _ensure_shorts_title(title)


def theme_overlay_label(meta: VideoMetadata, trend: str = "") -> str:
    """Короткая подпись на ролике (3–6 слов), не полное SEO-название."""
    thumb = re.sub(r"\s+", " ", (meta.thumbnail_title or "").strip())
    if thumb and 4 <= len(thumb) <= 42:
        return thumb[:42]
    from youtube_prayer.render import format_prayer_theme_label

    return format_prayer_theme_label(trend or _strip_shorts_suffix(meta.title), short=True)


_DEFAULT_BOT_USERNAME = "Talk_God_Bot"


def bot_start_link(*, day: str, index: int, lang: str = "ru") -> str:
    """Ссылка на клубного бота с меткой, какой ролик привёл человека.

    Клуб распознаёт payload yt_* как маркетинговое касание и кладёт его в
    users.first_touch_key. Так видно не «что набрало просмотры», а «что
    привело людей» — это разные ролики.
    """
    handle, direct = destination_for(lang)
    if direct:
        # Публичный канал: стартовой метки у него нет, ссылка как есть.
        return direct
    user = handle.lstrip("@") or _DEFAULT_BOT_USERNAME
    if not user:
        return ""
    tag = f"yt_{day.replace('-', '')}_{int(index):02d}"
    return f"https://t.me/{user}?start={tag}"


def _inject_bot_link(description: str, link: str, *, lang: str = "ru") -> str:
    """Ссылка — ПЕРВОЙ строкой описания.

    Раньше она стояла перед блоком Keywords, то есть после трёх абзацев.
    В плеере Shorts описание свёрнуто целиком, и до ссылки не доходил никто:
    за 37 тысяч просмотров в базе клуба ноль касаний с меткой yt_* — при
    том, что метка проставлялась верно и код её разбора стоял в проде.

    Первая строка — единственное место описания, которое вообще где-то
    показывается: на странице просмотра, в поиске и в превью канала.
    Рядом со ссылкой даём @имя текстом: из Shorts никуда не кликнуть,
    зато имя можно запомнить и найти в Telegram поиском.
    """
    if not link or link in description:
        return description
    name, _ = destination_for(lang)
    suffix = f" — {name}" if name else ""
    line = f"🙏 {profile(lang).outro_text}{suffix}: {link}"
    return f"{line}\n\n{description.lstrip()}"


def _order_hashtags(tags: list[str]) -> list[str]:
    """Тематические вперёд, #Shorts в хвост.

    YouTube показывает над заголовком только первые три хэштега описания —
    это единственная видимая часть. #Shorts там ничего не ищет и не даёт,
    а слот занимает.
    """
    rest = [t for t in tags if t.casefold() != "#shorts"]
    return rest + ["#Shorts"]


_ES_SYSTEM = """Escribes los metadatos de un YouTube Short: oraciones verticales cortas (1–2 min).
Respuesta ESTRICTAMENTE en JSON:
{{"title":"...","thumbnail_title":"...","description":"...","hashtags":["#Shorts",...]}}
{extra}
Español neutro latinoamericano. Usa «tú», nunca «vosotros». Sin regionalismos:
debe sonar natural en México, Colombia y Perú por igual.

title: 40–85 caracteres ANTES del sufijo #Shorts. Un gancho viral — dolor,
pregunta o reconocerse en ello.
Buenos formatos:
· «¿No puedes dormir? Una oración que calma el alma»
· «Cuando tus padres enferman — palabras que sostienen»
· «¿Angustia por los que amas? Una oración que vale escuchar»
PROHIBIDO: cortar en «—», imperativos después del guion, lenguaje burocrático,
listas de palabras clave sin emoción.

thumbnail_title: 3–6 palabras cortas para el texto SOBRE EL VIDEO (grande), sin #Shorts.

description: 2–3 párrafos cortos. OBLIGATORIO una línea aparte invitando a
escribir «Amén» en los comentarios — pero como intercambio, no como orden:
la persona escribe «Amén» y recibe algo a cambio (se ora por ella, su nombre
suena en la oración de hoy, se une a quienes oran). Nada de «dale like y
suscríbete» en lista: eso no funciona. Al final, Keywords tras ---.

hashtags: 6–10 etiquetas del tema de la oración. No escribas #Shorts, se añade solo."""


def _metadata_system_prompt(*, strict: bool = False, lang: str = "ru") -> str:
    extra = ""
    code = normalize_lang(lang)
    if code == "es":
        if strict:
            extra = (
                "\nEl title anterior estaba mal construido o no enganchaba. "
                "Reescríbelo desde cero: frase completa, gancho viral.\n"
            )
        return _ES_SYSTEM.format(extra=extra)
    if strict:
        extra = (
            "\nПРЕДЫДУЩИЙ title был грамматически кривым или нецепляющим. "
            "Перепиши с нуля: законченная фраза, вирусный крючок.\n"
        )
    return (
        "Ты пишешь метаданные для YouTube Shorts — коротких вертикальных молитв (1–2 мин). "
        "Ответ СТРОГО JSON:\n"
        '{"title":"...","thumbnail_title":"...","description":"...","hashtags":["#Shorts",...]}\n\n'
        + extra
        + "title: 40–85 символов ДО суффикса #Shorts. Вирусный крючок — боль, вопрос или узнавание.\n"
        "Хорошие форматы:\n"
        "· «Не можешь уснуть? Молитва, которая успокаивает»\n"
        "· «Когда родители болеют — слова, которые держат на плаву»\n"
        "· «Тревога за близких — молитва, которую стоит услышать»\n"
        "ЗАПРЕЩЕНО: обрыв на «—», «Молитва, когда X — обратись…», повелительное после тире, "
        "канцелярит, SEO-простыня без эмоции.\n"
        "thumbnail_title: 3–6 коротких слов для подписи НА ВИДЕО (крупно), без #Shorts.\n"
        "description: 2–3 коротких абзаца. ОБЯЗАТЕЛЬНО отдельной строкой "
        "призыв написать «Аминь» в комментариях — но как обмен, а не команду: "
        "человек пишет «Аминь» и получает что-то в ответ (за него помолятся, "
        "его имя прозвучит в молитве, он присоединяется к тем, кто молится "
        "сегодня). Не «поставьте лайк и подпишитесь» списком — это не работает. "
        "В конце Keywords через ---.\n"
        "hashtags: 6–10 тегов по теме молитвы. #Shorts не пиши, он добавится сам.\n"
    )


_FALLBACK_DESC = {
    "ru": (
        "Короткая молитва на 1–2 минуты: {brief}\n\n"
        "Спокойный голос, можно слушать с закрытыми глазами.\n"
        "Напиши «Аминь» в комментариях — и за тебя помолятся.\n\n"
        "---\n"
        "Keywords: молитва, shorts, христианская молитва, {trend}, вера, утешение"
    ),
    "en": (
        "A short 1–2 minute prayer: {brief}\n\n"
        "A calm voice — you can listen with your eyes closed.\n"
        "Write «Amen» in the comments and someone will pray for you.\n\n"
        "---\n"
        "Keywords: prayer, shorts, Christian prayer, {trend}, faith, comfort"
    ),
    "es": (
        "Una oración breve de 1 a 2 minutos: {brief}\n\n"
        "Voz tranquila — puedes escucharla con los ojos cerrados.\n"
        "Escribe «Amén» en los comentarios y alguien orará por ti.\n\n"
        "---\n"
        "Keywords: oración, shorts, oración cristiana, {trend}, fe, consuelo"
    ),
}


def _fallback_short_metadata(
    *, trend: str, brief: str, lang: str = "ru"
) -> VideoMetadata:
    code = normalize_lang(lang)
    title = _viral_fallback_title(trend, lang=code)
    core = _strip_shorts_suffix(title)
    desc = _FALLBACK_DESC[code].format(brief=brief, trend=trend)
    tags = _order_hashtags(list(profile(code).default_hashtags) + ["#Shorts"])
    return VideoMetadata(
        title=title,
        thumbnail_title=core[:42],
        description=desc,
        hashtags=tags,
    )


async def _parse_metadata_response(
    raw: Optional[str], *, trend: str, brief: str, lang: str = "ru"
) -> Optional[VideoMetadata]:
    try:
        data = _parse_json_obj(raw or "")
        title = _ensure_shorts_title(str(data.get("title") or ""))
        thumb = str(data.get("thumbnail_title") or "").strip()[:42]
        if not thumb:
            thumb = _strip_shorts_suffix(title)[:42]
        description = str(data.get("description") or "").strip()
        hashtags = _order_hashtags(
            _normalize_hashtags(data.get("hashtags") or [], lang=lang)
        )
        if len(title) < 8 or len(description) < 40:
            raise ValueError("metadata too short")
        if not _title_quality_ok(title):
            raise ValueError(f"title quality: {title!r}")
        return VideoMetadata(
            title=title,
            thumbnail_title=thumb,
            description=description,
            hashtags=hashtags[:12],
        )
    except Exception as e:
        logger.warning("short metadata parse failed trend=%r: %s", trend, e)
        return None


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


async def generate_short_metadata(
    *,
    trend: str,
    brief: str,
    complete_fn: CompleteFn,
    trend_pool: Optional[Sequence[str]] = None,
    work_dir: Optional[Path] = None,
    day: str = "",
    index: int = 0,
    lang: str = "ru",
) -> VideoMetadata:
    lang = normalize_lang(lang)
    today = datetime.now(_MSK).strftime("%d.%m.%Y")
    pool = [t for t in (trend_pool or []) if t and t != trend][:10]
    pool_txt = "\n".join(f"- {t}" for t in pool) if pool else "(нет доп. трендов)"
    user = (
        f"Дата (МСК): {today}\n"
        f"Тренд: {trend}\n"
        f"Бриф: {brief}\n"
        f"Другие тренды:\n{pool_txt}"
    )

    meta: Optional[VideoMetadata] = None
    for attempt, strict in enumerate((False, True)):
        raw = await complete_fn(_metadata_system_prompt(strict=strict, lang=lang), user)
        meta = await _parse_metadata_response(raw, trend=trend, brief=brief, lang=lang)
        if meta is not None:
            break
        if attempt == 0:
            user += (
                "\n\nПодсказка: title должен быть грамматически законченным и цепляющим, "
                "без «— обратись к Богу» и подобных обрывов."
            )

    if meta is None:
        meta = _fallback_short_metadata(trend=trend, brief=brief, lang=lang)

    if day and index:
        link = bot_start_link(day=day, index=index, lang=lang)
        if link:
            meta = replace(
                meta, description=_inject_bot_link(meta.description, link, lang=lang)
            )

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
