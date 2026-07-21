"""LLM: виральные моменты эфира — сначала мысль, затем окно 45–120 с."""

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
class ClipMoment:
    start_sec: float
    end_sec: float
    title: str
    hook: str
    reason: str
    # 0..100: насколько момент «цепляет» и подходит для шаринга.
    score: float = 0.0

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)


_SYSTEM = """Ты монтажёр Shorts/Reels. В расшифровке — ТОЛЬКО реплики Константина (Кости). Чужие реплики уже отфильтрованы.

Работай СТРОГО В ДВА ШАГА (не наоборот):

ШАГ 1 — ВЫБОР МЫСЛИ (самое важное).
Сначала просмотри ВЕСЬ массив речи Константина и выбери законченную, интересную и цепляющую мысль:
- одна ясная идея: начало → развитие → смысловой финал / вывод / удар;
- хочется дослушать / переслать / открыть полный эфир;
- НЕ «просто интересная фраза», НЕ приветствие, НЕ анонс, НЕ чужой вопрос без ответа Константина.
В reason сначала сформулируй мысль своими словами (1–2 предложения): о чём кусок и почему он «цепляет».
Только после этого переходи к шагу 2.

ШАГ 2 — НАРЕЗКА ПОД УЖЕ ВЫБРАННУЮ МЫСЛЬ.
Поставь start_sec / end_sec так, чтобы окно содержало ИМЕННО этот логический кусок целиком.
Длительность может быть ЛЮБОЙ в диапазоне {min_sec}–{max_sec} секунд — бери столько, сколько нужно мысли.
- Не режь мысль посередине ради «короче».
- Не дотягивай пустой болтовнёй до лимита.
- Убери края без мысли: оговорки, «ээ», повторы, уходы в сторону.
- start — с первого предложения, где мысль Константина уже началась; end — после смыслового завершения.

Выбери ровно {count} разных мыслей Константина (разные темы/углы, слабое пересечение по времени).
Без кликбейта. Тон: честный разговор с Богом, глубина.

Для КАЖДОГО момента оцени score (0..100): насколько хочется переслать, насколько мысль законченная, насколько усиливает идею эфира.

Верни ТОЛЬКО JSON:
{{
  "clips": [
    {{
      "start_sec": 412.0,
      "end_sec": 518.0,
      "title": "кратко: суть мысли",
      "hook": "цепляющая фраза для подписи в Telegram",
      "reason": "Усиление мысли для 3-й строки в Telegram (1–2 предложения). Не раскрывай весь выпуск и не делай спойлеров.",
      "score": 0.0
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

    skip_blocks = max(0, skip_first // 4)
    return format_expert_blocks_for_prompt(
        segments,
        limit_blocks=min(90, max(40, limit // 4)),
        skip_first_blocks=skip_blocks,
    )


def _clamp_moment(
    m: ClipMoment,
    *,
    min_sec: float,
    max_sec: float,
    max_end: float,
) -> ClipMoment:
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
    return ClipMoment(
        start_sec=start,
        end_sec=end,
        title=(m.title or "")[:120],
        hook=(m.hook or "")[:300],
        reason=(m.reason or "")[:400],
        score=float(getattr(m, "score", 0.0) or 0.0),
    )


def _parse_clips(
    raw: str,
    *,
    min_sec: float,
    max_sec: float,
    max_end: float,
) -> List[ClipMoment]:
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
    out: List[ClipMoment] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            cm = ClipMoment(
                start_sec=float(item.get("start_sec", 0)),
                end_sec=float(item.get("end_sec", 0)),
                title=str(item.get("title") or "").strip(),
                hook=str(item.get("hook") or "").strip(),
                reason=str(item.get("reason") or "").strip(),
                score=float(item.get("score") or 0.0),
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
) -> List[ClipMoment]:
    """Если LLM недоступен — самые длинные осмысленные окна."""
    if not segments:
        return []
    scored = sorted(
        segments,
        key=lambda s: len(s.text),
        reverse=True,
    )
    if regenerate:
        head = scored[: max(count * 4, 12)]
        random.shuffle(head)
        scored = head + [s for s in scored if s not in head]
    out: List[ClipMoment] = []
    used_starts: List[float] = []
    for seg in scored:
        if len(out) >= count:
            break
        if any(abs(seg.start_sec - u) < max_sec * 0.5 for u in used_starts):
            continue
        end = min(seg.end_sec + max_sec * 0.6, seg.start_sec + max_sec)
        if end - seg.start_sec < min_sec:
            end = seg.start_sec + min(max_sec, min_sec)
        out.append(
            ClipMoment(
                start_sec=seg.start_sec,
                end_sec=end,
                title=seg.text[:80],
                hook=seg.text[:160],
                reason="fallback: ясная мысль — хочется дослушать и поделиться",
                score=float(len(seg.text) or 0),
            )
        )
        used_starts.append(seg.start_sec)
    return out


def _rerank_candidates(
    candidates: List[ClipMoment],
    *,
    final_count: int,
    min_overlap_ratio: float = 0.35,
) -> List[ClipMoment]:
    """Выбираем топ по score, но отбрасываем клипы с сильным пересечением по времени."""
    if not candidates:
        return []

    def overlap_ratio(a: ClipMoment, b: ClipMoment) -> float:
        inter = max(0.0, min(a.end_sec, b.end_sec) - max(a.start_sec, b.start_sec))
        denom = max(1e-6, min(a.duration_sec, b.duration_sec))
        return inter / denom

    def norm_tokens(s: str) -> set[str]:
        return set(re.findall(r"[A-Za-zА-Яа-я0-9]+", (s or "").lower()))

    # Сортируем по score (сначала лучшие по мнению LLM).
    ordered = sorted(candidates, key=lambda c: float(c.score or 0.0), reverse=True)

    picked: List[ClipMoment] = []
    picked_tokens: List[set[str]] = []

    for c in ordered:
        if len(picked) >= final_count:
            break

        if any(overlap_ratio(c, p) >= min_overlap_ratio for p in picked):
            continue

        toks = norm_tokens(c.title + " " + c.hook)
        # грубая дедупликация по «похожим словам»
        if picked_tokens and toks:
            # Если слова почти те же — пропускаем.
            if max(
                (len(toks & pt) / max(1, len(toks | pt)) if pt else 0.0)
                for pt in picked_tokens
            ) >= 0.7:
                continue

        picked.append(c)
        picked_tokens.append(toks)

    return picked[:final_count]


async def pick_viral_moments(
    segments: Sequence[SpeechSegment],
    *,
    philosophy_hint: str,
    meeting_title: str,
    count: int = 5,
    max_duration_sec: int = 120,
    regenerate: bool = False,
) -> List[ClipMoment]:
    from config import config

    if not segments:
        return []

    max_sec = float(max(_MIN_CLIP_SEC, min(_MAX_CLIP_SEC, int(max_duration_sec))))
    min_sec = _MIN_CLIP_SEC
    max_end = max(s.end_sec for s in segments) + 5.0
    hint = (philosophy_hint or "").strip()
    title = (meeting_title or "Эфир").strip()
    variation = int(time.time()) % 10_000

    key = (config.OPENAI_API_KEY or "").strip()
    model = (getattr(config, "RAG_TAG_MODEL", None) or "gpt-4o-mini").strip()
    temperature = 0.92 if regenerate else 0.35

    n_seg = len(segments)
    window = max(40, min(90, n_seg // 3 or n_seg))
    step = max(20, window // 2)
    skip_offsets = list(range(0, max(1, n_seg - window + 1), step))
    if regenerate and n_seg > 20:
        skip_offsets = [random.randint(0, min(60, n_seg // 5))] + skip_offsets
    skip_offsets = skip_offsets[:5]
    per_window = max(8, (55 + len(skip_offsets) - 1) // max(1, len(skip_offsets)))

    all_candidates: List[ClipMoment] = []
    if key:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=key)
            for skip_first in skip_offsets:
                prompt_body = _segments_for_prompt(segments, skip_first=skip_first)
                if not prompt_body.strip():
                    continue
                user = (
                    f"Запись: {title}\n\n"
                    f"Источник: только речь Константина (Кости).\n"
                    f"Это ОДНО окно расшифровки (не весь эфир). "
                    f"Найди до {per_window} законченных цепляющих МЫСЛЕЙ Константина "
                    f"и поставь таймкоды ({int(min_sec)}–{int(max_sec)} с).\n"
                    f"Старайся не повторять одно и то же.\n\n"
                    f"Речь Константина блоками (таймкод → текст):\n{prompt_body}"
                )
                if regenerate:
                    user = (
                        f"Повторный запрос #{variation}. Нужны НОВЫЕ мысли.\n\n{user}"
                    )
                if hint:
                    user = f"Философия эксперта:\n{hint}\n\n{user}"
                sys_prompt = _SYSTEM.format(
                    count=per_window, min_sec=int(min_sec), max_sec=int(max_sec)
                )
                r = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user[:12000]},
                    ],
                    max_tokens=2200,
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
                all_candidates.extend(clips)
                logger.info(
                    "pick_viral_moments window skip=%s candidates=%s total=%s",
                    skip_first,
                    len(clips),
                    len(all_candidates),
                )
            if all_candidates:
                top = _rerank_candidates(all_candidates, final_count=count)
                logger.info(
                    "pick_viral_moments pool=%s → top=%s",
                    len(all_candidates),
                    len(top),
                )
                return top
        except Exception as e:
            logger.warning("pick_viral_moments LLM: %s", e)

    return _fallback_moments(
        segments,
        count=count,
        min_sec=min_sec,
        max_sec=max_sec,
        regenerate=regenerate,
    )[:count]
