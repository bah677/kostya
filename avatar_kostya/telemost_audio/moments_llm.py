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


class MomentsUnavailableError(RuntimeError):
    """LLM не смог выбрать моменты.

    Раньше на этом месте работал запасной механический отбор: он сортировал
    реплики по длине текста и брал самые длинные. Смысла он не понимал, и
    результат выглядел как случайные обрывки — но внешне неотличимо от
    нормальной работы, поэтому поломку не было видно. Лучше остановиться и
    сказать об этом прямо.
    """

_MIN_CLIP_SEC = 45.0
_MAX_CLIP_SEC = 120.0  # 2 мин
# Сколько знаков расшифровки отдаём модели за одно окно.
_WINDOW_CHARS = 18_000


@dataclass(frozen=True)
class AudioClipMoment:
    start_sec: float
    end_sec: float
    title: str
    hook: str
    reason: str
    # 0..100: насколько момент хорош для шаринга (и публикации).
    score: float = 0.0
    # Тема отрывка. Нужна, чтобы отсекать куски про одно и то же: раньше
    # разнообразие проверялось только по времени, и пять клипов могли
    # оказаться пятью нарезками одной мысли из разных мест эфира.
    theme: str = ""

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)


_SYSTEM = """Ты редактор аудио-подкастов. Тебе дают ОДНО ОКНО расшифровки эфира — не весь эфир. Твоя работа: вытащить из этого окна как можно больше сильных самостоятельных мыслей Константина.

Окон несколько, финальную пятёрку соберут из всех кандидатов. Поэтому здесь задача не «выбрать лучшее», а «ничего не упустить»: дай до {count} кандидатов. Чем больше разных мыслей найдёшь, тем лучше.

В расшифровке — ТОЛЬКО реплики Константина (Кости). Чужие реплики отфильтрованы: не выдумывай диалоги.

ЧТО СЧИТАЕТСЯ КАНДИДАТОМ.
Законченная мысль: начало → развитие → смысловой финал. Её можно слушать отдельно, не зная остального эфира, и она цепляет — хочется дослушать и открыть полный эфир.

ЧТО КАНДИДАТОМ НЕ ЯВЛЯЕТСЯ (не бери никогда):
- приветствие, знакомство, «как слышно», настройка на эфир;
- анонсы, оргвопросы, техническая часть;
- обрывки без начала или без финала.

РАЗНЫЕ МЫСЛИ, А НЕ ОДНА С РАЗНЫХ СТОРОН.
Два кандидата, которые описываются одной фразой, — это один кандидат. Если мысль длинная, возьми её целиком одним куском, а не режь на части.

ТАЙМКОДЫ.
Длительность любая в диапазоне {min_sec}–{max_sec} секунд — сколько нужно мысли.
- Не режь мысль посередине ради «короче», не добивай пустотой до лимита.
- Убери края без мысли: оговорки, повторы, уходы в сторону.
- start — где мысль реально началась; end — после смыслового завершения.

Для КАЖДОГО кандидата оцени score 0..100:
- насколько хочется переслать этот кусочек,
- насколько мысль завершённая и понятная,
- насколько она самостоятельна без остального эфира.

theme — о чём мысль, 2–4 слова. По ней потом отсекают дубли, поэтому формулируй так, чтобы разные мысли получили разные темы.
hook — ОДНО короткое яркое предложение для подписи в Telegram (до 120 символов). Без кликбейта. Тон: честный разговор с Богом.

Верни ТОЛЬКО JSON:
{{
  "clips": [
    {{
      "start_sec": 412.0,
      "end_sec": 518.0,
      "theme": "о чём мысль, 2-4 слова",
      "title": "суть мысли",
      "hook": "Одно предложение — почему стоит послушать.",
      "reason": "Усиление мысли для caption в Telegram (1–2 предложения). Без спойлеров.",
      "score": 0.0
    }}
  ]
}}

start/end — реальные секунды из расшифровки."""

_SYSTEM_POKAYANIE = """Ты редактор аудио-мини-подкастов покаяния. В расшифровке — диалог: речь Константина (Кости) И речь участника покаяния. Не выдумывай реплик и не отбрасывай участника.

Работай СТРОГО В ДВА ШАГА (не наоборот):

ШАГ 1 — ВЫБОР МОМЕНТА (самое важное).
Выбери законченный живой кусок покаяния:
- диалог Кости и участника (вопрос, ответ, разбор, наставление) ИЛИ цельная мысль Кости в этом диалоге;
- начало → развитие → смысловой финал;
- НЕ приветствие, НЕ техничку, НЕ обрывки.
В reason своими словами (1–2 предложения): о чём кусок и чем цепляет.
Только после этого переходи к шагу 2.

ШАГ 2 — НАРЕЗКА ПОД УЖЕ ВЫБРАННЫЙ КУСОК.
start_sec / end_sec покрывают этот кусок целиком, включая реплики участника, без которых мысль Кости не держится.
Длительность ЛЮБАЯ в диапазоне {min_sec}–{max_sec} секунд — столько, сколько нужно.
- Не режь посередине реплики или ответа.
- Не добивай пустотой до лимита.
- Убери края без смысла: оговорки, повторы, уходы в сторону.

Выбери ровно {count} разных кусков (слабое пересечение по времени).
hook — ОДНО короткое яркое предложение для подписи в Telegram (до 120 символов).
Без кликбейта. Тон: честный разговор с Богом.

Для КАЖДОГО выбранного момента оцени score по шкале 0..100:
- насколько хочется переслать/поделиться этим кусочком,
- насколько мысль/диалог завершенные и понятные,
- насколько «усиливает» суть покаяния.

Верни ТОЛЬКО JSON:
{{
  "clips": [
    {{
      "start_sec": 412.0,
      "end_sec": 518.0,
      "title": "суть мысли",
      "hook": "Одно предложение — почему стоит послушать.",
      "reason": "Усиление мысли для caption в Telegram (1–2 предложения). Не раскрывай весь эфир и не делай спойлеров.",
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
    """Окно расшифровки с таймкодами.

    Раньше здесь вызывался format_expert_blocks_for_prompt: он склеивает
    соседние реплики, если пауза между ними меньше 12 секунд, а потом режет
    каждый блок до 520 знаков. В связной речи весь эфир склеивался в ОДИН
    блок — и модель получала 530 знаков от 69-минутного эфира, а все окна
    после первого приходили пустыми. Отсюда и брались «кривые» нарезки:
    выбирать было не из чего, работал запасной механический отбор.

    Теперь окно режется по самим репликам, с таймкодом у каждой.
    """
    if not segments:
        return ""
    window = segments[skip_first : skip_first + max(1, int(limit))]
    lines: List[str] = []
    budget = _WINDOW_CHARS
    for seg in window:
        text = (seg.text or "").strip()
        if not text:
            continue
        mm, ss = divmod(int(seg.start_sec), 60)
        line = f"[{mm:02d}:{ss:02d}] {text}"
        budget -= len(line)
        if budget <= 0:
            break
        lines.append(line)
    return "\n".join(lines)


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
        score=float(getattr(m, "score", 0.0) or 0.0),
        theme=(getattr(m, "theme", "") or "")[:120],
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
                score=float(item.get("score") or 0.0),
                theme=str(item.get("theme") or "").strip(),
            )
            out.append(
                _clamp_moment(
                    cm, min_sec=min_sec, max_sec=max_sec, max_end=max_end
                )
            )
        except (TypeError, ValueError):
            continue
    return out


def _rerank_candidates(
    candidates: List[AudioClipMoment],
    *,
    final_count: int,
    min_overlap_ratio: float = 0.35,
    min_gap_sec: float = 120.0,
    theme_threshold: float = 0.45,
) -> List[AudioClipMoment]:
    """Топ по score: разные темы, без пересечений и не вплотную друг к другу.

    Раньше разнообразие держалось только на времени и на сходстве
    формулировок с порогом 0.7 — пять нарезок одной мысли из разных мест
    эфира проходили этот фильтр насквозь. Теперь основной признак — тема.
    """
    if not candidates:
        return []

    def overlap_ratio(a: AudioClipMoment, b: AudioClipMoment) -> float:
        inter = max(0.0, min(a.end_sec, b.end_sec) - max(a.start_sec, b.start_sec))
        denom = max(1e-6, min(a.duration_sec, b.duration_sec))
        return inter / denom

    def norm_tokens(s: str) -> set[str]:
        return set(re.findall(r"[A-Za-zА-Яа-я0-9]+", (s or "").lower()))

    ordered = sorted(candidates, key=lambda c: float(c.score or 0.0), reverse=True)
    picked: List[AudioClipMoment] = []
    picked_tokens: List[set[str]] = []

    for c in ordered:
        if len(picked) >= final_count:
            break
        if any(overlap_ratio(c, p) >= min_overlap_ratio for p in picked):
            continue
        # Соседние по времени куски — почти всегда одна мысль, разрезанная надвое
        if any(
            abs(c.start_sec - p.start_sec) < min_gap_sec for p in picked
        ):
            continue
        toks = norm_tokens(f"{c.theme} {c.title} {c.hook}")
        if picked_tokens and toks:
            # Порог ниже прежних 0.7: половины общих слов уже хватает,
            # чтобы считать темы одинаковыми.
            if max(
                (len(toks & pt) / max(1, len(toks | pt)) if pt else 0.0)
                for pt in picked_tokens
            ) >= theme_threshold:
                continue
        picked.append(c)
        picked_tokens.append(toks)

    return picked[:final_count]


_EDITOR_SYSTEM = """Ты главный редактор. Тебе дают список кандидатов — фрагментов одного эфира, которые отобрали помощники. Каждый смотрел только свой кусок расшифровки и не видел остальных, поэтому в списке много вариаций одной и той же мысли.

Твоя задача: выбрать ровно {count} фрагментов для публикации по отдельности, в разные дни.

ГЛАВНЫЙ КРИТЕРИЙ — РАЗНЫЕ ТЕМЫ.
Человек, послушавший все {count}, должен узнать {count} разных вещей. Два фрагмента про одно и то же — это один фрагмент, даже если оба сильные и сформулированы по-разному. Из группы похожих бери ОДИН, самый яркий, остальные выбрасывай.

ВТОРОЙ КРИТЕРИЙ — сила: законченная мысль, хочется переслать.

Разброс по времени эфира — хороший признак разных тем, но не цель сам по себе.

ЕСЛИ РАЗНЫХ ТЕМ МЕНЬШЕ, ЧЕМ {count}.
Значит эфир и правда про одно. Тогда верни меньше — столько, сколько реально разных тем набралось. Добивать список вариациями одной мысли нельзя.

Верни ТОЛЬКО JSON, номера из списка в порядке публикации:
{{"picked": [3, 17, 42], "why": "одной фразой, по какому признаку разводил темы"}}"""


async def _pick_diverse_with_llm(
    candidates: List[AudioClipMoment],
    *,
    count: int,
    model: str,
    api_key: str,
) -> List[AudioClipMoment]:
    """Финальный отбор: модель видит ВСЕХ кандидатов разом.

    Окна опрашиваются независимо, и если тема доминирует в эфире, она
    выигрывает в каждом окне. Отбор по score тогда даёт пять вариаций одной
    мысли — ровно то, на что жаловались. Разнести их может только тот, кто
    видит весь список сразу; по совпадению слов это не ловится, потому что
    формулировки у вариаций разные.
    """
    if len(candidates) <= count:
        return candidates

    from openai import AsyncOpenAI

    lines = []
    for i, c in enumerate(candidates):
        mm, ss = divmod(int(c.start_sec), 60)
        lines.append(
            f"{i}. [{mm:02d}:{ss:02d}] тема: {c.theme or '—'} | {c.title[:90]}"
        )
    client = AsyncOpenAI(api_key=api_key)
    resp = await client.chat.completions.create(
        model=model,
        temperature=0.3,
        messages=[
            {"role": "system", "content": _EDITOR_SYSTEM.format(count=count)},
            {"role": "user", "content": "Кандидаты:\n" + "\n".join(lines)},
        ],
        max_tokens=800,
    )
    try:
        from bot.services.llm_usage_tracker import log_from_response

        await log_from_response(
            resp, provider="openai", model=model, request_kind="audio_moments_editor"
        )
    except Exception:
        pass
    raw = (resp.choices[0].message.content or "").strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        raise ValueError("редактор вернул не JSON")
    data = json.loads(m.group(0))
    idx = [int(i) for i in (data.get("picked") or []) if isinstance(i, (int, float))]
    picked = [candidates[i] for i in idx if 0 <= i < len(candidates)]
    if data.get("why"):
        logger.info("editor: %s", str(data["why"])[:200])
    return picked[:count]


def _is_pokayanie(recording_kind: str) -> bool:
    from telemost_audio.recording_kind import KIND_POKAYANIE

    return (recording_kind or "").strip().lower() == KIND_POKAYANIE


async def pick_audio_moments(
    segments: Sequence[SpeechSegment],
    *,
    philosophy_hint: str,
    meeting_title: str,
    count: int = 5,
    max_duration_sec: int = 120,
    regenerate: bool = False,
    recording_kind: str = "",
) -> List[AudioClipMoment]:
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
    model = (getattr(config, "TELEMOST_AUDIO_MOMENTS_MODEL", None) or "gpt-4.1").strip()
    temperature = 0.9 if regenerate else 0.4

    # Несколько окон по расшифровке → ~50–60 кандидатов → локальный топ-N.
    n_seg = len(segments)
    window = max(40, min(90, n_seg // 3 or n_seg))
    step = max(20, window // 2)
    skip_offsets = list(range(0, max(1, n_seg - window + 1), step))
    if regenerate and n_seg > 20:
        skip_offsets = [random.randint(0, min(60, n_seg // 5))] + skip_offsets
    # Не больше 5 LLM-вызовов, чтобы не грузить сервер.
    skip_offsets = skip_offsets[:5]
    per_window = max(8, (55 + len(skip_offsets) - 1) // max(1, len(skip_offsets)))

    all_candidates: List[AudioClipMoment] = []
    if key:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=key)
            for skip_first in skip_offsets:
                prompt_body = _segments_for_prompt(segments, skip_first=skip_first)
                if not prompt_body.strip():
                    continue
                pokayanie = _is_pokayanie(recording_kind)
                if pokayanie:
                    user = (
                        f"Запись: {title}\n\n"
                        f"Источник: речь Константина (Кости) и речь участника покаяния.\n"
                        f"Это ОДНО окно расшифровки (не вся запись). "
                        f"Найди до {per_window} законченных кусков диалога "
                        f"и поставь таймкоды ({int(min_sec)}–{int(max_sec)} с).\n"
                        f"Старайся не повторять одно и то же.\n\n"
                        f"Диалог блоками (сек → кто говорит → текст):\n{prompt_body}"
                    )
                else:
                    user = (
                        f"Запись: {title}\n\n"
                        f"Источник: только речь Константина (Кости).\n"
                        f"Это ОДНО окно расшифровки (не весь эфир). "
                        f"Найди до {per_window} законченных цепляющих МЫСЛЕЙ Константина "
                        f"и поставь таймкоды ({int(min_sec)}–{int(max_sec)} с).\n"
                        f"Старайся не повторять одно и то же.\n\n"
                        f"Речь Константина блоками (сек → текст):\n{prompt_body}"
                    )
                if regenerate:
                    user = f"Повтор #{variation}: нужны ДРУГИЕ мысли.\n\n{user}"
                if hint:
                    user = f"Философия:\n{hint}\n\n{user}"
                sys_prompt = (_SYSTEM_POKAYANIE if pokayanie else _SYSTEM).format(
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
                try:
                    from bot.services.llm_usage_tracker import log_from_response

                    await log_from_response(
                        r,
                        provider="openai",
                        model=model,
                        request_kind="audio_moments_extract",
                    )
                except Exception:
                    pass
                out = r.choices[0].message.content if r.choices else ""
                clips = _parse_clips(
                    out or "",
                    min_sec=min_sec,
                    max_sec=max_sec,
                    max_end=max_end,
                )
                all_candidates.extend(clips)
                logger.info(
                    "pick_audio_moments window skip=%s candidates=%s total=%s",
                    skip_first,
                    len(clips),
                    len(all_candidates),
                )
            if all_candidates:
                # Сначала механически убираем пересечения по времени — в список
                # для редактора не должны попадать два куска одного отрезка.
                pool = _rerank_candidates(
                    all_candidates,
                    final_count=max(count * 6, 30),
                    min_gap_sec=0.0,
                    theme_threshold=0.95,
                )
                try:
                    top = await _pick_diverse_with_llm(
                        pool, count=count, model=model, api_key=key
                    )
                except Exception as e:
                    logger.warning(
                        "editor не сработал (%s), берём топ по score", e
                    )
                    top = pool[:count]
                logger.info(
                    "pick_audio_moments pool=%s → отобрано=%s",
                    len(all_candidates),
                    len(top),
                )
                if top:
                    return top
            raise MomentsUnavailableError(
                "LLM вернула пустой список кандидатов по всем окнам расшифровки"
            )
        except MomentsUnavailableError:
            raise
        except Exception as e:
            logger.warning("pick_audio_moments LLM: %s", e)
            raise MomentsUnavailableError(f"обращение к LLM не удалось: {e}") from e

    raise MomentsUnavailableError("OPENAI_API_KEY не задан — нарезка невозможна")
