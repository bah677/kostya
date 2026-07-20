"""LLM: мини-подкасты — сначала мысль, затем окно 45–120 с."""

from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import List, Sequence

from telemost_mail.timestamped_speech import SpeechSegment

logger = logging.getLogger(__name__)

_MIN_CLIP_SEC = 45.0
_MAX_CLIP_SEC = 120.0  # 2 мин


@dataclass(frozen=True)
class AudioClipMoment:
    start_sec: float
    end_sec: float
    title: str
    hook: str
    reason: str

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)


_SYSTEM = """Ты редактор аудио-мини-подкастов. В расшифровке — ТОЛЬКО реплики Константина (Кости). Чужие реплики уже отфильтрованы: не выдумывай диалоги и не бери «общие» куски.

Работай СТРОГО В ДВА ШАГА (не наоборот):

ШАГ 1 — ВЫБОР МЫСЛИ (самое важное).
Сначала просмотри ВЕСЬ массив речи Константина и выбери законченную, интересную и цепляющую мысль:
- одна ясная идея: начало → развитие → смысловой финал / вывод;
- хочется дослушать до конца и открыть полный эфир;
- НЕ приветствие, НЕ анонс, НЕ обрывки, НЕ чужие вопросы без ответа Константина.
В reason сначала сформулируй мысль своими словами (1–2 предложения): о чём и чем цепляет.
Только после этого переходи к шагу 2.

ШАГ 2 — НАРЕЗКА ПОД УЖЕ ВЫБРАННУЮ МЫСЛЬ.
Поставь start_sec / end_sec так, чтобы окно покрывало эту мысль целиком.
Длительность ЛЮБАЯ в диапазоне {min_sec}–{max_sec} секунд — столько, сколько нужно мысли.
- Не режь мысль посередине ради «короче».
- Не добивай пустотой до лимита.
- Убери края без мысли: оговорки, повторы, уходы в сторону.
- start — где мысль Константина реально началась; end — после смыслового завершения.

Выбери ровно {count} разных мыслей Константина (слабое пересечение по времени).
hook — ОДНО короткое яркое предложение для подписи в Telegram (до 120 символов).
Без кликбейта. Тон: честный разговор с Богом.

Верни ТОЛЬКО JSON:
{{
  "clips": [
    {{
      "start_sec": 412.0,
      "end_sec": 518.0,
      "title": "суть мысли",
      "hook": "Одно предложение — почему стоит послушать.",
      "reason": "Мысль: … Почему цепляет: …"
    }}
  ]
}}

start/end — реальные секунды из расшифровки."""


def _segments_for_prompt(
    segments: Sequence[SpeechSegment],
    limit: int = 400,
    *,
    skip_first: int = 0,
) -> str:
    from telemost_mail.timestamped_speech import format_expert_blocks_for_prompt

    # limit раньше был по репликам; для блоков берём сопоставимый объём
    skip_blocks = max(0, skip_first // 4)
    return format_expert_blocks_for_prompt(
        segments,
        limit_blocks=min(90, max(40, limit // 4)),
        skip_first_blocks=skip_blocks,
    )


def _clamp_moment(
    m: AudioClipMoment,
    *,
    min_sec: float,
    max_sec: float,
    max_end: float,
) -> AudioClipMoment:
    start = max(0.0, float(m.start_sec))
    end = min(float(m.end_sec), max_end)
    if end <= start:
        end = min(start + max_sec, max_end)
    dur = end - start
    if dur > max_sec:
        end = start + max_sec
        dur = end - start
    if dur < min_sec and end < max_end:
        end = min(start + min_sec, max_end)
    hook = (m.hook or m.title or "").strip()
    if hook.count(".") > 1:
        hook = hook.split(".")[0].strip() + "."
    if len(hook) > 120:
        hook = hook[:117].rstrip() + "…"
    return AudioClipMoment(
        start_sec=start,
        end_sec=end,
        title=(m.title or "")[:120],
        hook=hook[:120],
        reason=(m.reason or "")[:400],
    )


def _parse_clips(
    raw: str,
    *,
    min_sec: float,
    max_sec: float,
    max_end: float,
) -> List[AudioClipMoment]:
    text = (raw or "").strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    items = data.get("clips") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out: List[AudioClipMoment] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            cm = AudioClipMoment(
                start_sec=float(item.get("start_sec", 0)),
                end_sec=float(item.get("end_sec", 0)),
                title=str(item.get("title") or "").strip(),
                hook=str(item.get("hook") or item.get("title") or "").strip(),
                reason=str(item.get("reason") or "").strip(),
            )
            out.append(
                _clamp_moment(
                    cm, min_sec=min_sec, max_sec=max_sec, max_end=max_end
                )
            )
        except (TypeError, ValueError):
            continue
    return out


def _fallback_moments(
    segments: Sequence[SpeechSegment],
    *,
    count: int,
    min_sec: float,
    max_sec: float,
    regenerate: bool = False,
) -> List[AudioClipMoment]:
    if not segments:
        return []
    scored = sorted(segments, key=lambda s: len(s.text), reverse=True)
    if regenerate:
        head = scored[: max(count * 4, 12)]
        random.shuffle(head)
        scored = head + [s for s in scored if s not in head]
    out: List[AudioClipMoment] = []
    used: List[float] = []
    for seg in scored:
        if len(out) >= count:
            break
        if any(abs(seg.start_sec - u) < max_sec * 0.45 for u in used):
            continue
        end = min(seg.end_sec + max_sec * 0.5, seg.start_sec + max_sec)
        if end - seg.start_sec < min_sec:
            end = seg.start_sec + min(max_sec, min_sec)
        hook = seg.text.split(".")[0].strip()[:120]
        if hook and not hook.endswith((".", "!", "?", "…")):
            hook += "."
        out.append(
            AudioClipMoment(
                start_sec=seg.start_sec,
                end_sec=end,
                title=seg.text[:80],
                hook=hook or seg.text[:120],
                reason="fallback",
            )
        )
        used.append(seg.start_sec)
    return out


async def pick_audio_moments(
    segments: Sequence[SpeechSegment],
    *,
    philosophy_hint: str,
    meeting_title: str,
    count: int = 5,
    max_duration_sec: int = 120,
    regenerate: bool = False,
) -> List[AudioClipMoment]:
    from config import config

    if not segments:
        return []

    max_sec = float(max(_MIN_CLIP_SEC, min(_MAX_CLIP_SEC, int(max_duration_sec))))
    min_sec = _MIN_CLIP_SEC
    max_end = max(s.end_sec for s in segments) + 5.0
    skip_first = 0
    if regenerate and len(segments) > 20:
        skip_first = random.randint(0, min(60, len(segments) // 5))
    prompt_body = _segments_for_prompt(segments, skip_first=skip_first)
    hint = (philosophy_hint or "").strip()
    title = (meeting_title or "Эфир").strip()
    variation = int(time.time()) % 10_000

    user = (
        f"Запись: {title}\n\n"
        f"Источник: только речь Константина (Кости).\n"
        f"Сначала из всего массива выбери {count} законченных цепляющих МЫСЛЕЙ Константина, "
        f"и только потом под каждую мысль поставь таймкоды нарезки "
        f"({int(min_sec)}–{int(max_sec)} с).\n\n"
        f"Речь Константина блоками (сек → текст):\n{prompt_body}"
    )
    if regenerate:
        user = (
            f"Повтор #{variation}: нужны ДРУГИЕ мысли и таймкоды.\n\n{user}"
        )
    if hint:
        user = f"Философия:\n{hint}\n\n{user}"

    key = (config.OPENAI_API_KEY or "").strip()
    model = (getattr(config, "RAG_TAG_MODEL", None) or "gpt-4o-mini").strip()
    temperature = 0.9 if regenerate else 0.4
    if key and prompt_body:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=key)
            sys_prompt = _SYSTEM.format(
                count=count, min_sec=int(min_sec), max_sec=int(max_sec)
            )
            if regenerate:
                sys_prompt += "\n\nВыбери другие мысли и фрагменты, чем в прошлый раз."
            r = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user[:12000]},
                ],
                max_tokens=1600,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            out = r.choices[0].message.content if r.choices else ""
            clips = _parse_clips(
                out or "",
                min_sec=min_sec,
                max_sec=max_sec,
                max_end=max_end,
            )
            if clips:
                return clips[:count]
        except Exception as e:
            logger.warning("pick_audio_moments LLM: %s", e)

    return _fallback_moments(
        segments,
        count=count,
        min_sec=min_sec,
        max_sec=max_sec,
        regenerate=regenerate,
    )[:count]
