"""Google Trends (RU) → TOP, затем LLM отбор тем под молитву."""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional, Sequence

import httpx

from youtube_prayer.prompts import TREND_FILTER_SYSTEM

logger = logging.getLogger(__name__)

_TRENDS_RSS = "https://trends.google.com/trending/rss?geo=RU"
_DAILY_RSS = (
    "https://trends.google.com/trends/trendingsearches/daily/rss?geo=RU"
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


async def fetch_google_trends_ru(*, limit: int = 12) -> List[str]:
    """TOP трендов RU (RSS). При сбое — пустой список."""
    titles: List[str] = []
    timeout = httpx.Timeout(25.0, connect=10.0)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; AvatarYTPrayer/1.0)",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        for url in (_TRENDS_RSS, _DAILY_RSS):
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


def _fallback_topics(n: int) -> List[PrayerTopic]:
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
    ]
    return seeds[: max(1, n)]


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


async def select_prayer_topics(
    trends: Sequence[str],
    *,
    n: int,
    complete_fn,
) -> List[PrayerTopic]:
    """
    complete_fn(system, user) -> Optional[str]
    """
    n = max(1, min(5, int(n)))
    if not trends:
        logger.warning("trends empty — fallback topics")
        return _fallback_topics(n)

    listed = "\n".join(f"{i}. {t}" for i, t in enumerate(trends[:12], 1))
    user = (
        f"N={n}\nТренды (RU):\n{listed}\n\n"
        f"Выбери ровно {n} тем для молитв."
    )
    raw = await complete_fn(TREND_FILTER_SYSTEM, user)
    topics = _parse_topics_json(raw or "", n=n)
    if len(topics) < n:
        logger.warning(
            "trend filter returned %s/%s — дополняем fallback",
            len(topics),
            n,
        )
        for fb in _fallback_topics(n):
            if len(topics) >= n:
                break
            if all(fb.trend != t.trend for t in topics):
                topics.append(fb)
    return topics[:n]
