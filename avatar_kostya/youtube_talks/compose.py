"""Тема разговорного ролика и развёрнутый ответ из Писания."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

from youtube_prayer.compose import deepseek_complete
from youtube_prayer.langs import normalize_lang
from youtube_talks import prompts

logger = logging.getLogger(__name__)

# Замер на живой озвучке: 1080 знаков дали 119.5 с при atempo 0.85, то есть
# 101.6 с в обычном темпе — примерно 10.6 знака в секунду. Отсюда и границы.
CHARS_PER_SEC = 10.6

MIN_SEC = 45
# Предел Shorts — 180 секунд; за ним YouTube считает ролик обычным видео.
# Держим запас: призыв в конце тоже занимает время, и реальная озвучка может
# разойтись с оценкой на несколько секунд.
MAX_TOTAL_SEC = 155
MIN_CHARS = 700          # ≈ 66 с — с запасом над требуемыми 45
MAX_CHARS = 1450         # ≈ 137 с, плюс призыв ≈ 12 с
_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class TalkTopic:
    topic: str
    why: str
    visual: str

    def slug_source(self) -> str:
        return self.topic


def est_seconds(text: str) -> float:
    return len(text or "") / CHARS_PER_SEC


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


async def pick_talk_topic(
    *,
    lang: str = "ru",
    trends: Sequence[str] = (),
    recent: Sequence[str] = (),
) -> TalkTopic:
    """Одна житейская тема. Тренды — подсказка, основа — список канала.

    Тренды Google почти всегда новости и спорт: на молитвенном пайплайне из них
    проходит 0 пригодных тем из 3. Поэтому список канала здесь не запасной
    вариант, а основной источник.
    """
    code = normalize_lang(lang)
    pool = [t for t in prompts.topics_for(code) if t not in set(recent)]
    if not pool:
        pool = prompts.topics_for(code)
    trend_block = "\n".join(f"- {t}" for t in list(trends)[:20]) or "(ничего)"
    recent_block = "\n".join(f"- {t}" for t in list(recent)[:40]) or "(пусто)"
    user = (
        f"Темы канала:\n" + "\n".join(f"- {t}" for t in pool[:25]) + "\n\n"
        f"Google Trends:\n{trend_block}\n\n"
        f"Уже использовали:\n{recent_block}"
    )
    try:
        raw, _ = await deepseek_complete(
            prompts.topic_pick_system(code),
            user,
            temperature=0.8,
            max_tokens=500,
            thinking="disabled",
        )
        data = _parse_json_obj(raw or "")
        topic = str(data.get("topic") or "").strip()
        if topic:
            return TalkTopic(
                topic=topic,
                why=str(data.get("why") or "").strip(),
                visual=str(data.get("visual") or "").strip() or "calm nature soft light",
            )
    except Exception as e:
        logger.warning("talks: выбор темы не удался (%s) — беру из списка", e)
    import random

    return TalkTopic(topic=random.choice(pool), why="", visual="calm nature soft light")


class TalkAnswerTooShort(RuntimeError):
    """Ответ короче минимума даже после повторов — ролик не делаем.

    Молча выпускать десятисекундный «разбор» хуже, чем пропустить день:
    такой ролик портит и канал, и статистику формата.
    """


async def compose_answer(topic: TalkTopic, *, lang: str = "ru") -> str:
    """Развёрнутый ответ из Писания. Длину держим в границах по знакам."""
    code = normalize_lang(lang)
    system = prompts.answer_system(
        code,
        min_chars=MIN_CHARS,
        max_chars=MAX_CHARS,
        min_sec=int(MIN_CHARS / CHARS_PER_SEC),
        max_sec=int(MAX_CHARS / CHARS_PER_SEC),
    )
    ask = {
        "ru": (
            "Человек спрашивает: что говорит Писание про «{topic}» и как с этим "
            "справиться? Расскажи развёрнуто."
        ),
        "en": (
            "A person asks: what does Scripture say about «{topic}» and how does "
            "one cope with it? Answer at length."
        ),
        "es": (
            "Una persona pregunta: ¿qué dice la Escritura sobre «{topic}» y cómo "
            "sobrellevarlo? Responde con desarrollo."
        ),
    }[code].format(topic=topic.topic)
    if topic.why:
        ask += f"\n\n{topic.why}"

    longer = {
        "ru": "\n\nПРЕДЫДУЩИЙ ответ был слишком короткий. Разверни сильнее: больше конкретики в практической части.",
        "en": "\n\nThe previous answer was too short. Develop it further: more specifics in the practical part.",
        "es": "\n\nLa respuesta anterior fue demasiado corta. Desarróllala más: más concreción en la parte práctica.",
    }[code]

    best = ""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        user = ask if attempt == 1 else ask + longer
        raw, finish = await deepseek_complete(
            system,
            user,
            temperature=0.7,
            max_tokens=2400,
            thinking="disabled",
        )
        text = _clean(raw or "")
        if len(text) > len(best):
            best = text
        logger.info(
            "talks answer attempt=%s chars=%s (~%.0f с) finish=%s topic=%r",
            attempt,
            len(text),
            est_seconds(text),
            finish,
            topic.topic,
        )
        if len(text) >= MIN_CHARS:
            return _clamp_long(text)
    if len(best) >= int(MIN_SEC * CHARS_PER_SEC):
        # Короче цели, но длиннее минимума по ТЗ — выпускаем.
        logger.warning(
            "talks: ответ %s знаков (~%.0f с) — короче цели, но выше минимума",
            len(best),
            est_seconds(best),
        )
        return _clamp_long(best)
    raise TalkAnswerTooShort(
        f"ответ {len(best)} знаков (~{est_seconds(best):.0f} с), "
        f"нужно минимум {int(MIN_SEC * CHARS_PER_SEC)}"
    )


def _clean(raw: str) -> str:
    t = (raw or "").strip()
    t = re.sub(r"^```(?:\w+)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    # Разметка в озвучке читается вслух как мусор.
    t = re.sub(r"</?[a-zA-Z][^>]*>", "", t)
    t = re.sub(r"[*_#`]+", "", t)
    t = re.sub(r"^\s*[-•—]\s+", "", t, flags=re.M)
    t = re.sub(r"^\s*\d+[.)]\s+", "", t, flags=re.M)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip().strip('"').strip("«»")


_LATIN_RE = re.compile(r"\b[A-Za-z]{3,}\b")


def warn_foreign_words(text: str, *, lang: str, topic: str) -> None:
    """Латиница в русском тексте — её озвучка прочитает как есть.

    Ловили живьём: в ответе про долги появилось «Что делать practically».
    Не падаем из-за этого, но в лог пишем: если повторяется, надо править промпт.
    """
    if normalize_lang(lang) != "ru":
        return
    found = _LATIN_RE.findall(text or "")
    if found:
        logger.warning(
            "talks: латиница в русском тексте %r — %s", topic, ", ".join(found[:5])
        )


def _clamp_long(text: str, *, limit: Optional[int] = None) -> str:
    """Обрезаем по границе предложения, а не по знаку посреди слова."""
    cap = MAX_CHARS if limit is None else limit
    if len(text) <= cap:
        return text
    head = text[:cap]
    cut = max(head.rfind("."), head.rfind("!"), head.rfind("?"))
    if cut > cap * 0.6:
        return head[: cut + 1].strip()
    return head.rstrip() + "."


def spoken_text(answer: str, *, lang: str = "ru") -> str:
    """Ответ + призыв в конце. Между ними пауза подольше — как в молитвах.

    Призыв входит в бюджет длительности: если вместе с ним ролик вылезает за
    MAX_TOTAL_SEC, подрезаем ответ, а не призыв — он и есть смысл ролика.
    """
    cta = prompts.cta_text(lang)
    body = (answer or "").rstrip()
    if not cta:
        return body
    budget = int(MAX_TOTAL_SEC * CHARS_PER_SEC) - len(cta) - 10
    if len(body) > budget > 200:
        body = _clamp_long(body, limit=budget)
        logger.info("talks: ответ подрезан до %s знаков под бюджет озвучки", len(body))
    return f"{body}\n\n...\n\n{cta}"
