"""LLM: продающие подписи к голосовым — заголовок, суть, цитата из Библии, CTA."""

from __future__ import annotations

import json
import logging
import re
import secrets
import string
from dataclasses import dataclass
from html import escape as html_escape
from typing import List, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.utils.telegram_html import sanitize_telegram_html
from telemost_audio.moments_llm import AudioClipMoment
from telemost_mail.timestamped_speech import SpeechSegment

logger = logging.getLogger(__name__)

_REF_ALPHABET = string.ascii_lowercase + string.digits
_REF_SUFFIX_LEN = 10

_SYSTEM = """Ты копирайтер духовного клуба «ЛЮБЯЩИЕ БОГА». Пишешь подписи к голосовым отрывкам эфира в Telegram.

ЧТО ТАКОЕ ХОРОШАЯ ПОДПИСЬ.
Она не рассказывает, что внутри. Она называет то, что человек уже про себя знает, но не формулировал — и обещает, что дальше будет поворот. Человек должен узнать себя в первой строке и нажать play, чтобы услышать продолжение.

Плохо (пересказ, всё уже сказано):
  «Костя говорит о том, что важно доверять Богу в трудностях»
  «Размышление о молитве и терпении»
Хорошо (узнавание + незакрытый разрыв):
  «Ты молишься, а тишина в ответ. Костя объясняет, что это значит»
  «Мы просим у Бога сил. А Он даёт обстоятельства, в которых они появляются»

ПРАВИЛА.
- Пиши о слушателе, не о спикере. «Ты», а не «автор рассказывает».
- Называй конкретное состояние: не «трудности», а «третью ночь не спишь».
- Разрыв не закрывай: назови вопрос, ответ оставь в аудио. Но и не обманывай — обещай ровно то, что в отрывке есть.
- Никакого кликбейта, capslock и эмодзи-мусора. Тон — честный разговор, а не реклама.
- Опирайся только на текст отрывка. Ничего не добавляй от себя.

ПОЛЯ.
- headline — до 80 символов. Крючок: состояние, вопрос или узнавание. НЕ тема отрывка.
- summary — 1–2 предложения, до 220 символов. Разверни напряжение из headline и доведи до края, за которым идёт аудио. Не список «о чём тут».
- bible_quote — короткая цитата из Библии в кавычках «…» (до 180 символов), по смыслу отрывка.
- bible_ref — место Писания, например «Иоан. 3:16» или «Псалом 22:1».

Стиль: тёплый, живой, про отношения с Богом. Без markdown и HTML — только plain text в полях JSON.

Верни ТОЛЬКО JSON:
{{
  "captions": [
    {{
      "clip_index": 0,
      "headline": "...",
      "summary": "...",
      "bible_quote": "«...»",
      "bible_ref": "..."
    }}
  ]
}}"""


@dataclass(frozen=True)
class AudioClipCaption:
    headline: str
    summary: str
    bible_quote: str
    bible_ref: str
    html_text: str
    ref_code: str
    keyboard: InlineKeyboardMarkup


def generate_audio_ref_code() -> str:
    suffix = "".join(secrets.choice(_REF_ALPHABET) for _ in range(_REF_SUFFIX_LEN))
    return f"ref_ac_{suffix}"


def club_deep_link(ref_code: str) -> str:
    from config import config

    username = (
        getattr(config, "TELEMOST_AUDIO_CLUB_BOT_USERNAME", "") or "Talk_God_Bot"
    ).strip().lstrip("@")
    return f"https://t.me/{username}?start={ref_code}"


def club_button(ref_code: str) -> InlineKeyboardMarkup:
    from config import config

    label = (
        getattr(config, "TELEMOST_AUDIO_CLUB_BUTTON_TEXT", "")
        or "Любящие Бога"
    ).strip()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, url=club_deep_link(ref_code))]
        ]
    )


def _moment_transcript(
    segments: Sequence[SpeechSegment],
    moment: AudioClipMoment,
    *,
    limit: int = 3000,
) -> str:
    """Текст отрывка для копирайтера.

    Лимит был 600 знаков от начала. Клип на 45–120 секунд — это 900–2200
    знаков, то есть до концовки текст не доходил: подпись писалась по первой
    трети, без развязки. Отсюда и брались пересказы вместо крючков.
    Если всё же не влезает — оставляем начало И конец, финал важнее середины.
    """
    start = float(moment.start_sec)
    end = float(moment.end_sec)
    parts: List[str] = []
    for seg in segments:
        if seg.end_sec < start or seg.start_sec > end:
            continue
        parts.append(seg.text.strip())
    text = " ".join(p for p in parts if p).strip()
    if not text:
        text = (moment.title or moment.hook or "").strip()
    if len(text) <= limit:
        return text
    head = int(limit * 0.55)
    tail = limit - head - 20
    return f"{text[:head].rstrip()} […] {text[-tail:].lstrip()}"


def _format_html_caption(
    headline: str,
    summary: str,
    bible_quote: str,
    bible_ref: str,
) -> str:
    hq = html_escape((headline or "Фрагмент эфира").strip())
    sq = html_escape((summary or "").strip())
    bq = html_escape((bible_quote or "").strip())
    br = html_escape((bible_ref or "").strip())

    quote_block = bq
    if br:
        quote_block = f"{bq}\n{br}" if bq else br

    lines = [
        f"<b>{hq}</b>",
        "",
        sq,
        "",
        f"<blockquote>{quote_block}</blockquote>",
        "",
        "Полная запись эфира уже хранится в архиве клуба <b>ЛЮБЯЩИЕ БОГА</b>.",
    ]
    text = sanitize_telegram_html("\n".join(line for line in lines if line is not None))
    if len(text) > 1024:
        text = text[:1021].rstrip() + "…"
    return text


def _fallback_caption(
    moment: AudioClipMoment,
    segments: Sequence[SpeechSegment],
) -> AudioClipCaption:
    ref_code = generate_audio_ref_code()
    headline = (moment.hook or moment.title or "Послушай этот отрывок эфира").strip()
    excerpt = _moment_transcript(segments, moment, limit=220)
    summary = excerpt.split(".")[0].strip()
    if summary and not summary.endswith((".", "!", "?", "…")):
        summary += "."
    if not summary:
        summary = headline
    if len(summary) > 200:
        summary = summary[:197].rstrip() + "…"

    return AudioClipCaption(
        headline=headline[:80],
        summary=summary,
        bible_quote="«Ибо где двое или трое собраны во имя Моё, там Я посреди них»",
        bible_ref="Матф. 18:20",
        html_text=_format_html_caption(
            headline[:80],
            summary,
            "«Ибо где двое или трое собраны во имя Моё, там Я посреди них»",
            "Матф. 18:20",
        ),
        ref_code=ref_code,
        keyboard=club_button(ref_code),
    )


def _parse_caption_items(raw: str, count: int) -> List[dict]:
    text = (raw or "").strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    items = data.get("captions") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out: List[dict] = []
    for item in items:
        if isinstance(item, dict):
            out.append(item)
    return out[:count]


async def build_audio_captions(
    moments: Sequence[AudioClipMoment],
    segments: Sequence[SpeechSegment],
    *,
    meeting_title: str = "",
    philosophy_hint: str = "",
) -> List[AudioClipCaption]:
    """Генерирует подписи и уникальный ref-код для каждого голосового."""
    if not moments:
        return []

    clips_payload = []
    for i, moment in enumerate(moments):
        clips_payload.append(
            {
                "clip_index": i,
                "title": moment.title,
                "hook": moment.hook,
                "transcript_excerpt": _moment_transcript(segments, moment),
            }
        )

    parsed: List[dict] = []
    from config import config

    key = (config.OPENAI_API_KEY or "").strip()
    model = (getattr(config, "TELEMOST_AUDIO_CAPTION_MODEL", None) or "gpt-4.1").strip()
    title = (meeting_title or "Эфир").strip()
    hint = (philosophy_hint or "").strip()

    user_lines = [f"Эфир: {title}", "", "Отрывки:"]
    for item in clips_payload:
        user_lines.append(
            f"\n--- clip {item['clip_index']} ---\n"
            f"Тема: {item['title']}\n"
            f"Hook: {item['hook']}\n"
            f"Текст:\n{item['transcript_excerpt']}"
        )
    if hint:
        user_lines.insert(1, f"Философия клуба:\n{hint}\n")

    if key:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=key)
            r = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": "\n".join(user_lines)[:14000]},
                ],
                max_tokens=1800,
                temperature=0.65,
                response_format={"type": "json_object"},
            )
            out = r.choices[0].message.content if r.choices else ""
            parsed = _parse_caption_items(out or "", len(moments))
        except Exception as e:
            logger.warning("build_audio_captions LLM: %s", e)

    by_index: dict[int, dict] = {}
    for item in parsed:
        try:
            idx = int(item.get("clip_index", -1))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(moments):
            by_index[idx] = item

    captions: List[AudioClipCaption] = []
    for i, moment in enumerate(moments):
        ref_code = generate_audio_ref_code()
        item = by_index.get(i)
        if not item:
            captions.append(_fallback_caption(moment, segments))
            continue
        headline = str(item.get("headline") or moment.hook or moment.title or "").strip()
        summary = str(item.get("summary") or moment.hook or "").strip()
        bible_quote = str(item.get("bible_quote") or "").strip()
        bible_ref = str(item.get("bible_ref") or "").strip()
        if not headline:
            headline = moment.hook or moment.title or "Фрагмент эфира"
        if not summary:
            summary = moment.hook or headline
        if not bible_quote:
            bible_quote = "«Господь близок ко всем призывающим Его»"
            bible_ref = bible_ref or "Псалом 144:18"
        headline = headline[:80]
        summary = summary[:220]
        bible_quote = bible_quote[:180]
        bible_ref = bible_ref[:40]
        captions.append(
            AudioClipCaption(
                headline=headline,
                summary=summary,
                bible_quote=bible_quote,
                bible_ref=bible_ref,
                html_text=_format_html_caption(
                    headline,
                    summary,
                    bible_quote,
                    bible_ref,
                ),
                ref_code=ref_code,
                keyboard=club_button(ref_code),
            )
        )
    return captions
