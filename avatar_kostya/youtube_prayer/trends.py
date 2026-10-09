"""Google Trends (RU) → TOP, затем LLM отбор тем под молитву."""

from __future__ import annotations

from youtube_prayer.langs import normalize_lang

import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional, Sequence

import httpx

from youtube_prayer.prompts import TREND_FILTER_SYSTEM, TREND_SCORE_SYSTEM

logger = logging.getLogger(__name__)

_TRENDS_RSS = "https://trends.google.com/trending/rss?geo={geo}"
_DAILY_RSS = (
    "https://trends.google.com/trends/trendingsearches/daily/rss?geo={geo}"
)


@dataclass(frozen=True)
class PrayerTopic:
    trend: str
    brief: str
    broll_query: str


def _titles_from_rss(xml_text: str) -> List[str]:
    out: List[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for item in root.findall(".//item"):
        title_el = item.find("title")
        title = (title_el.text or "").strip() if title_el is not None else ""
        if title and title not in out:
            out.append(title)
    return out


async def fetch_google_trends(*, geo: str = "RU", limit: int = 12) -> List[str]:
    """TOP трендов по geo (RSS). При сбое — пустой список."""
    geo = (geo or "RU").strip().upper() or "RU"
    titles: List[str] = []
    timeout = httpx.Timeout(25.0, connect=10.0)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; AvatarYTPrayer/1.0)",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    urls = [
        _TRENDS_RSS.format(geo=geo),
        _DAILY_RSS.format(geo=geo),
    ]
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        for url in urls:
            try:
                r = await client.get(url)
                if r.status_code >= 400:
                    logger.warning("trends RSS HTTP %s url=%s", r.status_code, url)
                    continue
                chunk = _titles_from_rss(r.text)
                for t in chunk:
                    if t not in titles:
                        titles.append(t)
                if len(titles) >= limit:
                    break
            except Exception as e:
                logger.warning("trends fetch failed url=%s: %s", url, e)
    return titles[:limit]


async def fetch_google_trends_ru(*, limit: int = 12) -> List[str]:
    return await fetch_google_trends(geo="RU", limit=limit)


async def fetch_google_trends_us(*, limit: int = 12) -> List[str]:
    return await fetch_google_trends(geo="US", limit=limit)


# Приписка к заданию отбора: на каком языке формулировать темы.
_LANG_NOTE = {
    "ru": "",
    "en": " Brief и формулировки — на английском.",
    "es": " Brief и формулировки — на испанском (нейтральный латиноамериканский).",
}

# Запасной brief, когда модель не вернула своего.
_BRIEF_TMPL = {
    "ru": (
        "Молитва о сердце человека в связи с темой «{cand}»: "
        "утешение, сила и надежда на Бога."
    ),
    "en": (
        "A prayer for the human heart around «{cand}»: "
        "comfort, strength, and hope in God."
    ),
    "es": (
        "Una oración por el corazón humano en torno a «{cand}»: "
        "consuelo, fuerza y esperanza en Dios."
    ),
}


def _fallback_topics(
    n: int,
    *,
    exclude: Sequence[str] = (),
    lang: str = "ru",
) -> List[PrayerTopic]:
    code = normalize_lang(lang)
    if code == "es":
        # Запасные темы на случай, когда тренды ничего пригодного не дали.
        # Раньше их было только две ветки — en и «всё остальное, то есть
        # русский», и испанский ролик выходил на русскую тему.
        seeds = [
            PrayerTopic(
                "ansiedad y preocupación",
                "Una oración por la paz interior y la libertad de la ansiedad.",
                "calm ocean sunrise soft light",
            ),
            PrayerTopic(
                "cansancio y agotamiento",
                "Una oración por descanso, fuerzas renovadas y esperanza.",
                "quiet forest mist morning",
            ),
            PrayerTopic(
                "soledad y cercanía",
                "Una oración por un corazón que vuelve a sentirse acompañado.",
                "warm candle light window rain",
            ),
            PrayerTopic(
                "la paz en la familia",
                "Una oración por la paz en casa, el perdón y el amor.",
                "soft sunset field peaceful",
            ),
            PrayerTopic(
                "esperanza en un día difícil",
                "Una oración por la fe y la luz en los días que pesan.",
                "golden hour sky clouds slow",
            ),
            PrayerTopic(
                "gratitud por lo sencillo",
                "Una oración de gratitud por un día común y el cuidado de Dios.",
                "sunlight through leaves gentle",
            ),
            PrayerTopic(
                "perdonar y reconciliarse",
                "Una oración por la fuerza de perdonar y recuperar la paz.",
                "quiet lake reflection dawn",
            ),
            PrayerTopic(
                "miedo a lo que viene",
                "Una oración por confiar en Dios cuando el futuro no está claro.",
                "mountain path fog soft light",
            ),
            PrayerTopic(
                "no poder dormir",
                "Una oración para las noches en que la mente no se apaga.",
                "night sky stars slow calm",
            ),
        ]
    elif code == "en":
        seeds = [
            PrayerTopic(
                "anxiety and worry",
                "A prayer for inner peace and freedom from anxiety.",
                "calm ocean sunrise soft light",
            ),
            PrayerTopic(
                "burnout and exhaustion",
                "A prayer for rest, restored strength, and hope.",
                "quiet forest mist morning",
            ),
            PrayerTopic(
                "loneliness and belonging",
                "A prayer for warmth of heart, acceptance, and close relationships.",
                "warm candle light window rain",
            ),
            PrayerTopic(
                "family peace at home",
                "A prayer for peace in the family, forgiveness, and love.",
                "soft sunset field peaceful",
            ),
            PrayerTopic(
                "hope in a hard day",
                "A prayer for strength of faith and light in a difficult day.",
                "golden hour sky clouds slow",
            ),
            PrayerTopic(
                "gratitude for small joys",
                "A prayer of thanks for a simple day and God's care.",
                "sunlight through leaves gentle",
            ),
            PrayerTopic(
                "forgiveness and reconciliation",
                "A prayer for strength to forgive and restore peace in the heart.",
                "quiet lake reflection dawn",
            ),
            PrayerTopic(
                "fear of the unknown",
                "A prayer for trust in God when the future is unclear.",
                "mountain path fog soft light",
            ),
        ]
    else:
        seeds = [
            PrayerTopic(
                "тревога и беспокойство",
                "Молитва о внутреннем мире и свободе от тревоги.",
                "calm ocean sunrise soft light",
            ),
            PrayerTopic(
                "усталость и выгорание",
                "Молитва о покое, восстановлении сил и надежде.",
                "quiet forest mist morning",
            ),
            PrayerTopic(
                "одиночество и близость",
                "Молитва о тепле сердца, принятии и близких отношениях.",
                "warm candle light window rain",
            ),
            PrayerTopic(
                "семья и мир дома",
                "Молитва о мире в семье, прощении и любви.",
                "soft sunset field peaceful",
            ),
            PrayerTopic(
                "надежда в трудный день",
                "Молитва о силе веры и свете в тяжёлый день.",
                "golden hour sky clouds slow",
            ),
            PrayerTopic(
                "благодарность за малые радости",
                "Молитва благодарности за простой день и Божью заботу.",
                "sunlight through leaves gentle",
            ),
            PrayerTopic(
                "прощение и примирение",
                "Молитва о силе простить и восстановить мир в сердце.",
                "quiet lake reflection dawn",
            ),
            PrayerTopic(
                "страх неизвестности",
                "Молитва о доверии Богу, когда будущее неясно.",
                "mountain path fog soft light",
            ),
        ]
    from youtube_prayer.topic_history import trends_similar

    out: List[PrayerTopic] = []
    for s in seeds:
        if any(trends_similar(s.trend, r) for r in exclude):
            continue
        out.append(s)
        if len(out) >= n:
            break
    if len(out) < n:
        for s in seeds:
            if any(trends_similar(s.trend, x.trend) for x in out):
                continue
            out.append(s)
            if len(out) >= n:
                break
    return out[: max(1, n)]


def _parse_topics_json(raw: str, *, n: int) -> List[PrayerTopic]:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    items = data.get("topics") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out: List[PrayerTopic] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        trend = str(it.get("trend") or "").strip()
        brief = str(it.get("brief") or "").strip()
        broll = str(it.get("broll_query") or "calm nature sky").strip()
        if not trend or not brief:
            continue
        out.append(PrayerTopic(trend=trend, brief=brief, broll_query=broll or "calm nature"))
        if len(out) >= n:
            break
    return out


def _parse_score_json(raw: str) -> dict[str, bool]:
    """trend -> suitable."""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {}
    out: dict[str, bool] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        trend = str(it.get("trend") or "").strip()
        if not trend:
            continue
        suitable = it.get("suitable")
        if isinstance(suitable, str):
            suitable = suitable.strip().lower() in {"1", "true", "yes", "да"}
        out[trend] = bool(suitable)
    return out


def _heuristic_reject(trend: str) -> bool:
    """Быстрый hard-reject явного мусора до/после LLM (подстраховка)."""
    t = (trend or "").casefold().replace("ё", "е")
    bad_substrings = (
        "рубл",
        "доллар",
        "биткоин",
        "крипт",
        "акци",
        "футбол",
        "хоккей",
        "матч",
        "выбор",
        "путин",
        "зеленск",
        "санкц",
        "iphone",
        "samsung",
        "курс ",
        "котиров",
        "предпенсион",
        "пенсионн",  # часто сухой термин; LLM может одобрить «страх старости» отдельно
    )
    # имя+фамилия / известные короткие имена как весь тренд — грубо
    if re.fullmatch(r"[а-яa-z]+ [а-яa-z]+", t) and " " in t:
        # «андрей рублев», «илон маск» — два слова-имени обычно не молитва
        # но «мир дома» ок; эвристика: если нет слов из whitelist переживаний
        feeling = (
            "тревог",
            "страх",
            "одиноч",
            "любов",
            "семья",
            "семьи",
            "горе",
            "боль",
            "мир",
            "вера",
            "надежд",
            "устало",
            "выгоран",
            "болезн",
            "депресс",
            "бессон",
            "прощен",
            "отношен",
            "детей",
            "родител",
            "брак",
            "развод",
        )
        if not any(f in t for f in feeling):
            return True
    return any(b in t for b in bad_substrings)


async def select_prayer_topics(
    trends: Sequence[str],
    *,
    n: int,
    complete_fn,
    recent_themes: Sequence[str] = (),
    lang: str = "ru",
) -> List[PrayerTopic]:
    """
    1) LLM score suitable/unsuitable по каждому тренду
    2) LLM pick brief только из suitable
    3) если мало — curated fallback (НЕ сырые тренды)
    """
    from youtube_prayer.topic_history import filter_out_recent, trends_similar

    lang = (lang or "ru").lower()
    n = max(1, min(12, int(n)))
    recent = [str(x).strip() for x in recent_themes if str(x).strip()]
    fresh = filter_out_recent(trends, recent)
    pool = [t for t in (fresh if fresh else list(trends)) if not _heuristic_reject(t)]

    suitable: List[str] = []
    if pool:
        listed = "\n".join(f"{i}. {t}" for i, t in enumerate(pool[:24], 1))
        score_user = (
            "Оцени каждый тренд. Не подстраивайся под квоту — честный suitable.\n\n"
            f"Тренды:\n{listed}"
        )
        score_raw = await complete_fn(TREND_SCORE_SYSTEM, score_user)
        scores = _parse_score_json(score_raw or "")
        for t in pool:
            ok = scores.get(t)
            if ok is None:
                for k, v in scores.items():
                    if trends_similar(t, k):
                        ok = v
                        break
            if ok is True:
                suitable.append(t)
            elif ok is False:
                logger.info("trend rejected by LLM: %r", t)
            else:
                logger.info("trend unscored → reject: %r", t)

        logger.info(
            "trend score lang=%s: pool=%s suitable=%s → %s",
            lang,
            len(pool),
            len(suitable),
            suitable,
        )

    topics: List[PrayerTopic] = []
    if suitable:
        recent_block = (
            "\n".join(f"- {t}" for t in recent[:40]) if recent else "(пока пусто)"
        )
        approved = "\n".join(f"{i}. {t}" for i, t in enumerate(suitable[:20], 1))
        pick_user = (
            f"N={n}\n"
            f"Недавно использовали:\n{recent_block}\n\n"
            f"Одобренные тренды:\n{approved}\n\n"
            f"Выбери до {n} тем только из одобренных."
            + _LANG_NOTE.get(normalize_lang(lang), "")
        )
        pick_raw = await complete_fn(TREND_FILTER_SYSTEM, pick_user)
        picked = _parse_topics_json(pick_raw or "", n=n * 2)
        approved_set = suitable
        for t in picked:
            if _heuristic_reject(t.trend):
                continue
            if not any(trends_similar(t.trend, a) for a in approved_set):
                logger.warning("LLM invented/non-approved trend skipped: %r", t.trend)
                continue
            if any(trends_similar(t.trend, r) for r in recent):
                continue
            if any(trends_similar(t.trend, x.trend) for x in topics):
                continue
            topics.append(t)
            if len(topics) >= n:
                break
        if len(topics) < n:
            for cand in suitable:
                if len(topics) >= n:
                    break
                if any(trends_similar(cand, t.trend) for t in topics):
                    continue
                if any(trends_similar(cand, r) for r in recent):
                    continue
                brief = _BRIEF_TMPL[normalize_lang(lang)].format(cand=cand)
                topics.append(
                    PrayerTopic(
                        trend=cand,
                        brief=brief,
                        broll_query="calm nature soft light",
                    )
                )

    if len(topics) < n:
        logger.warning(
            "only %s/%s prayer-suitable topics from trends — fallback human themes",
            len(topics),
            n,
        )
        for fb in _fallback_topics(
            n + 5,
            exclude=list(recent) + [t.trend for t in topics],
            lang=lang,
        ):
            if len(topics) >= n:
                break
            topics.append(fb)

    return topics[:n]
