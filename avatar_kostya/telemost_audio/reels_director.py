"""Параллельно с нарезкой: готовые тексты Reels по полной расшифровке → топик.

Пайплайн (ТЗ):
  1. Map-reduce по кускам речи Кости → JSON-идеи с anchor_sec + score → топ-N.
  2. Для идеи: окно дословной речи ±pad сек.
  3. Скрытый JSON-план (режиссура) → конкуренция хуков → 2–3 тела → судья.
  4. Рендер в шаблон + пост-проверка дословных фраз.
  5. Рубрика DeepSeek (с окном расшифровки), до 2 раундов, принимать только рост.
  6. Сохранение в БД + кнопки (оценка / сгенерить Short в топик YouTube).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import string
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.utils.rag_admin_context import rag_reels_brief_chat_topic
from config import config
from telemost_audio.recording_kind import (
    KIND_LABELS,
    recording_kind_from_pending,
    wants_shorts_clips,
)
from telemost_mail.timestamped_speech import (
    SpeechSegment,
    format_expert_blocks_for_prompt,
    merge_segments_window,
    parse_expert_segments,
)

logger = logging.getLogger(__name__)

_active: set[str] = set()
_TG_CHUNK = 3900
_CHUNK_CHARS = 22_000
_WINDOW_PAD_DEFAULT = 75.0
_TOP_IDEAS_DEFAULT = 5
_HOOK_N_DEFAULT = 6
_BODY_N_DEFAULT = 2
_REVIEW_ROUNDS_DEFAULT = 2
_RUBRIC_AXIS_MIN = 7.0
_VERBATIM_MIN_HITS = 2
_REF_ALPHABET = string.ascii_lowercase + string.digits

CB_REELS_FB = "rlsfb:"  # rlsfb:{action}:{uuid}  action=ok|no|gen

# ── Промпты ──────────────────────────────────────────────────────────────────

_SYSTEM_EXTRACT = """Ты — редактор контента. В куске — ТОЛЬКО речь Константина (Кости) с таймкодами [A–B s].

Выпиши сильные самодостаточные мысли для вирусного короткого видео (Reels/Shorts).
Ниша: духовное развитие.

Критерии:
- Узнавание («это про меня»), хочется переслать.
- Самодостаточна без всего эфира.
- Конкретная, не абстрактная вода.
- Основана на реальных словах куска (не выдумывать).

Для каждой мысли укажи:
- title — краткий заголовок;
- quote — дословная цитата 1–2 предложения из куска;
- anchor_sec — секунда-якорь ВНУТРИ интервала [A–B] куска (число, середина мысли);
- score — 0..100: сила для Reels (досмотр, шаринг, ясность);
- reason — почему сильная (1 предложение).

Верни ТОЛЬКО JSON:
{"ideas":[{"title":"...","quote":"...","anchor_sec":412.0,"score":78,"reason":"..."}]}

Если сильных мыслей нет — {"ideas":[]}. Пиши по-русски."""

_SYSTEM_PLAN = """Ты — режиссёр коротких видео. НЕ пиши готовый текст рилса.

По мысли и дословной расшифровке окна составь скрытый план виральности.
Тип хука выбери один: accusation | confession | direct_question | paradox.

Верни ТОЛЬКО JSON:
{
  "hook_type": "paradox",
  "first_3_sec_promise": "что зритель получает в первые 3 секунды",
  "tension": "где напряжение",
  "turn": "где поворот",
  "loop_close": "чем закрывается петля",
  "cta": "club|bot|channel|soft_none",
  "must_use_phrases": ["дословная фраза 1 из окна", "фраза 2", "фраза 3"]
}

must_use_phrases — 2–3 короткие дословные фразы из окна (как сказал Костя).
Пиши по-русски в значениях JSON."""

_SYSTEM_HOOKS = """Ты копирайтер Reels. Дай варианты ТОЛЬКО первой фразы (хук на 3 секунды).

Каждая строка — отдельный хук, 5–14 слов, разговорный голос Кости.
Без таймингов, без пояснений.

Верни ТОЛЬКО JSON:
{"hooks":["...","..."]}"""

_SYSTEM_JUDGE_HOOKS = """Выбери лучший хук для досмотра первых 3 секунд.
Верни ТОЛЬКО JSON: {"best_index":0,"reason":"..."}"""

_SYSTEM_RENDER = """Ты — сценарист Reels. Ниша: духовное развитие. Пишешь текст, который человек досматривает до конца и пересылает.

ТЫ ПИШЕШЬ СЦЕНАРИЙ, А НЕ ПЕРЕСКАЗЫВАЕШЬ РАСШИФРОВКУ.
Окно расшифровки — сырьё: живая речь со сбоями, повторами, оборванными фразами и ошибками распознавания. Копировать её подряд нельзя. Так делать НЕЛЬЗЯ:
  «И я скажу видел. Видел чудеса, как люди меняются. Как меняются те. Кого простили? Не способен дьявол.»
Это не сценарий, это стенограмма. Из неё надо собрать связную речь.

ДОСЛОВНОСТЬ — ТОЧЕЧНАЯ.
Возьми 2–3 КОРОТКИЕ фразы Кости, каждая НЕ ДЛИННЕЕ 12 СЛОВ. Это самое сильное, что он сказал, — вставь без изменений, они держат голос. Всё остальное пиши сам.

Длинную цитату брать нельзя. Так НЕЛЬЗЯ:
  «Ты не сможешь Богу, не получится такого, что ты можешь Богу сказать, слушай, я свои обязательства выполнил, мое сердце чистое и непорочно, но продолжает происходить какая-то дичь»
Это 30 слов сырой речи с оборванным началом. Из неё берётся ядро:
  «продолжает происходить какая-то дичь в адрес меня»
Если сильная фраза оборвана или бессвязна — вытащи из неё чистое ядро, остальное перескажи своими словами.

ДЛИНА — СКОЛЬКО НУЖНО МЫСЛИ.
Считать слова не надо. Правило одно: ни одного лишнего слова. Если фразу можно убрать и ничего не потеряется — убери. Сильная мысль на сорок слов лучше растянутой на полтораста.

ЧТО ДЕЛАЕТ РИЛС ВИРУСНЫМ. Это главное, остальное — детали.

Первые три секунды решают всё. Хук должен открыть петлю, которую невозможно оставить незакрытой: назвать то, что человек прячет, или сказать то, с чем он мгновенно не согласен. «Сегодня поговорим о прощении» — петля не открыта, человек ушёл.

Дальше напряжение только растёт. Не объясняй сразу — сначала сделай больно узнаванием. Человек должен подумать «откуда он про меня знает».

Поворот должен быть неочевидным. Если вывод можно было предсказать с первой секунды, рилс не перешлют. Ищи в окне то, что переворачивает привычное: не «надо прощать», а почему непрощение бьёт по деньгам и детям.

Финальная фраза — то, что человек процитирует другу. Ради неё и пересылают. Она должна работать отдельно от всего остального.

Говори с одним человеком, а не с аудиторией. «Ты», а не «мы» и не «друзья».

СТРОЕНИЕ ТЕКСТА.
1. Первая фраза — выбранный хук, слово в слово.
2. Узнавание: конкретная ситуация, в которой человек себя видит.
3. Поворот: то, чего он не ждал. Здесь и стоят дословные фразы Кости.
4. Финал: фраза, которую уносят с собой. Её ты пишешь САМ — из расшифровки финал не берётся. Костя в эфире говорит дальше, и его переход к следующей теме («подготовлю эфир об этом», «слушай полный эфир») в рилсе звучит как оборванная запись. Закрой мысль сам. Без «подписывайтесь».

НЕ ПОВТОРЯЙСЯ. Одна и та же мысль дважды («я видел, как это работает» — и снова «я видел») съедает секунды, которых и так мало.

ЯЗЫК.
Короткие предложения. Обращение на «ты». Никакой воды и общих слов вроде «важно помнить». Конкретика: не «трудности», а то, что реально происходит с человеком.

ЗАПРЕЩЕНО в выводе:
- тайминги, таймкоды, «хук на 3 секунде», «захват внимания»;
- указания на паузы, интонации, планы, музыку, монтаж;
- объяснения структуры и любые пометки от себя.

ОПИСАНИЯ ПОД РИЛС — ровно ТРИ варианта, все три обязательны, по 1–2 предложения. Каждый ведёт в своё место и цепляется за тему ролика, а не за общие слова. Схлопывать их в один нельзя.

CTA живёт только в описаниях. В «Тексте рилса» никаких «полный эфир в клубе» и «подписывайся» — там только сама мысль.

Формат — СТРОГО:

Обложка: [3–5 слов, крючок]

Текст рилса: [90–140 слов]

Описание под рилс:
— Вариант 1 (на клуб): [текст]
— Вариант 2 (на библейского бота): [текст]
— Вариант 3 (на телеграм-канал): [текст]

Пиши по-русски."""

_SYSTEM_JUDGE_BODIES = """Сравни варианты сценария Reels. Выбери лучший по:
сила первых 3 сек / конкретность / голос Кости / опора на эфир / уместный CTA.
Верни ТОЛЬКО JSON: {"best_index":0,"reason":"..."}"""

_SYSTEM_RUBRIC = """Ты — редактор коротких видео. Перекрёстная проверка сценария Reels.

Оцени по 5 осям 0..10:
- hook_3s — сила первых 3 секунд;
- specificity — конкретность vs вода;
- kostya_voice — голос Кости (живой, не ChatGPT);
- air_fidelity — опора на реальный эфир (есть дословные куски);
- cta_fit — уместность CTA.

Правки (edits) — ТОЛЬКО по осям строго ниже порога {axis_min}. Если все оси ≥ порога — edits=[].
Не переписывай сценарий целиком.

Верни ТОЛЬКО JSON:
{{
  "scores": {{"hook_3s":0,"specificity":0,"kostya_voice":0,"air_fidelity":0,"cta_fit":0}},
  "total": 0,
  "edits": ["замени…", "усиль…"]
}}"""

_DEFAULT_PROJECT_DESC = """Проект Константина (Кости) в нише духовного развития:
- Клуб «Разговоры с Богом» — закрытое сообщество для духовных бесед и практики
- Бот «Ответ из Библии» — персональные молитвы и ответы из Писания
- Телеграм-канал с эфирами и размышлениями

Цель Reels: виральность, удержание досмотра, мягкий переход на один из продуктов проекта."""


@dataclass
class ReelIdea:
    title: str
    quote: str
    anchor_sec: float
    score: float = 0.0
    reason: str = ""

    def display(self) -> str:
        return f"{self.title} | {self.quote}".strip(" |")


@dataclass
class ScenarioBundle:
    text: str
    plan: Dict[str, Any] = field(default_factory=dict)
    hooks_alt: List[str] = field(default_factory=list)
    rubric: Dict[str, Any] = field(default_factory=dict)
    window: str = ""
    idea: Optional[ReelIdea] = None


# ── Публичный API ────────────────────────────────────────────────────────────

def enqueue_telemost_reels_brief(
    bot_app: Any,
    pending_id: uuid.UUID,
    row: Dict[str, Any],
    meta: Dict[str, Any],
    *,
    recording_kind: str = "",
    force: bool = False,
) -> bool:
    if not getattr(config, "TELEMOST_REELS_BRIEF_ENABLED", True):
        return False
    kind = (recording_kind or "").strip().lower() or recording_kind_from_pending(
        row, meta
    )
    if not wants_shorts_clips(kind):
        return False
    pid = str(pending_id)
    if pid in _active and not force:
        logger.info("telemost_reels_brief: already running pending_id=%s", pid)
        return False
    _active.add(pid)
    asyncio.create_task(
        _run_reels_brief(bot_app, pending_id, row, meta, recording_kind=kind),
        name=f"telemost_reels_brief_{pid[:8]}",
    )
    return True


async def run_reels_brief_for_row(
    bot_app: Any,
    row: Dict[str, Any],
    *,
    recording_kind: str = "",
) -> None:
    pending_id = row.get("id") or uuid.uuid4()
    if not isinstance(pending_id, uuid.UUID):
        try:
            pending_id = uuid.UUID(str(pending_id))
        except Exception:
            pending_id = uuid.uuid4()
    clf = row.get("classification") or {}
    meta: Dict[str, Any] = {}
    if isinstance(clf, dict):
        meta = clf.get("extra") or {}
    kind = (recording_kind or "").strip().lower() or recording_kind_from_pending(
        row, meta
    )
    await _run_reels_brief(bot_app, pending_id, row, meta, recording_kind=kind)


def feedback_keyboard(scenario_id: uuid.UUID) -> InlineKeyboardMarkup:
    """Оценка + сборка Short из текста сценария."""
    sid = str(scenario_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👍 Хороший", callback_data=f"{CB_REELS_FB}ok:{sid}"
                ),
                InlineKeyboardButton(
                    text="👎 Не подходит", callback_data=f"{CB_REELS_FB}no:{sid}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎬 Сгенерить видео",
                    callback_data=f"{CB_REELS_FB}gen:{sid}",
                ),
            ],
        ]
    )


def parse_feedback_cb(data: str) -> Optional[Tuple[str, uuid.UUID]]:
    raw = (data or "").strip()
    if not raw.startswith(CB_REELS_FB):
        return None
    rest = raw[len(CB_REELS_FB) :]
    if ":" not in rest:
        return None
    action, sid = rest.split(":", 1)
    action = action.strip().lower()
    if action not in ("ok", "no", "gen"):
        return None
    try:
        return action, uuid.UUID(sid.strip())
    except ValueError:
        return None


# ── Конфиг-хелперы ───────────────────────────────────────────────────────────

def _target_topic() -> tuple[int, Optional[int]]:
    chat, topic = rag_reels_brief_chat_topic()
    if not chat or not topic:
        logger.warning(
            "telemost_reels_brief: не задан чат/топик (TELEMOST_REELS_BRIEF_TOPIC_ID)"
        )
        return 0, None
    return int(chat), int(topic)


def _get_openai_model() -> str:
    return (
        (getattr(config, "TELEMOST_REELS_BRIEF_MODEL", None) or "gpt-4.1").strip()
        or "gpt-4.1"
    )


def _get_max_tokens() -> int:
    return int(getattr(config, "TELEMOST_REELS_BRIEF_MAX_TOKENS", 16000) or 16000)


def _crosscheck_enabled() -> bool:
    return bool(getattr(config, "TELEMOST_REELS_BRIEF_CROSSCHECK_ENABLED", True))


def _get_review_model() -> str:
    return (
        (getattr(config, "TELEMOST_REELS_BRIEF_REVIEW_MODEL", None) or "deepseek-chat").strip()
        or "deepseek-chat"
    )


def _project_description() -> str:
    custom = (getattr(config, "TELEMOST_REELS_BRIEF_PROJECT_DESC", None) or "").strip()
    if custom:
        return custom
    hint = (getattr(config, "TELEMOST_SHORTS_PHILOSOPHY_HINT", None) or "").strip()
    if hint:
        return f"{_DEFAULT_PROJECT_DESC}\n\nДополнительно:\n{hint}"
    return _DEFAULT_PROJECT_DESC


def _speaker_names() -> List[str]:
    raw = getattr(config, "TELEMOST_MAIL_AVATAR_SPEAKER_NAMES", "") or ""
    return [s.strip() for s in raw.split(",") if s.strip()] or ["Константин", "Костя"]


def _cfg_int(name: str, default: int, *, lo: int, hi: int) -> int:
    try:
        v = int(getattr(config, name, default) or default)
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def _cfg_float(name: str, default: float) -> float:
    try:
        return float(getattr(config, name, default) or default)
    except (TypeError, ValueError):
        return default


def _top_ideas_n() -> int:
    return _cfg_int("TELEMOST_REELS_TOP_IDEAS", _TOP_IDEAS_DEFAULT, lo=1, hi=12)


def _hook_n() -> int:
    return _cfg_int("TELEMOST_REELS_HOOK_VARIANTS", _HOOK_N_DEFAULT, lo=3, hi=10)


def _body_n() -> int:
    return _cfg_int("TELEMOST_REELS_BODY_VARIANTS", _BODY_N_DEFAULT, lo=1, hi=4)


def _window_pad() -> float:
    return max(30.0, min(120.0, _cfg_float("TELEMOST_REELS_WINDOW_PAD_SEC", _WINDOW_PAD_DEFAULT)))


def _review_rounds() -> int:
    return _cfg_int("TELEMOST_REELS_REVIEW_ROUNDS", _REVIEW_ROUNDS_DEFAULT, lo=0, hi=3)


def _new_ref_code() -> str:
    suffix = "".join(secrets.choice(_REF_ALPHABET) for _ in range(10))
    return f"ref_rl_{suffix}"


# ── LLM вызовы ───────────────────────────────────────────────────────────────

async def _call_openai_messages(
    client: Any,
    *,
    model: str,
    messages: List[Dict[str, str]],
    max_tokens: int,
    temperature: float = 0.7,
    response_json: bool = False,
) -> str:
    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max(800, min(32000, max_tokens)),
    }
    if response_json:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = await client.chat.completions.create(**kwargs)
    except Exception as e:
        err = str(e).lower()
        if response_json and "response_format" in err:
            kwargs.pop("response_format", None)
            resp = await client.chat.completions.create(**kwargs)
        elif model != "gpt-4o" and "model" in err and (
            "not found" in err or "does not exist" in err
        ):
            logger.warning("telemost_reels_brief: %s недоступна, fallback gpt-4o", model)
            kwargs["model"] = "gpt-4o"
            kwargs["max_tokens"] = max(800, min(16000, max_tokens))
            resp = await client.chat.completions.create(**kwargs)
        else:
            raise
    try:
        from bot.services.llm_usage_tracker import log_from_response

        await log_from_response(
            resp,
            provider="openai",
            model=str(kwargs.get("model") or model),
            request_kind="reels_openai",
        )
    except Exception as e:
        logger.debug("reels openai usage: %s", e)
    choice = resp.choices[0] if resp.choices else None
    text = (
        (getattr(choice.message, "content", None) or "").strip() if choice else ""
    )
    if not text:
        raise RuntimeError("OpenAI вернул пустой ответ")
    return text


async def _call_openai(
    client: Any,
    *,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float = 0.7,
    response_json: bool = False,
) -> str:
    return await _call_openai_messages(
        client,
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
        response_json=response_json,
    )


def _parse_json_obj(raw: str) -> dict:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}


# ── Извлечение идей (map-reduce) ─────────────────────────────────────────────

def _split_expert_blocks(blocks_text: str, *, max_chars: int = _CHUNK_CHARS) -> List[str]:
    parts = [p.strip() for p in (blocks_text or "").split("\n\n") if p.strip()]
    if not parts:
        return []
    chunks: List[str] = []
    buf: List[str] = []
    n = 0
    for p in parts:
        add = len(p) + 2
        if buf and n + add > max_chars:
            chunks.append("\n\n".join(buf))
            buf = [p]
            n = len(p)
        else:
            buf.append(p)
            n += add
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


def _parse_ideas_json(raw: str) -> List[ReelIdea]:
    data = _parse_json_obj(raw)
    out: List[ReelIdea] = []
    for item in data.get("ideas") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        quote = str(item.get("quote") or "").strip()
        if not title and not quote:
            continue
        try:
            anchor = float(item.get("anchor_sec") or 0)
        except (TypeError, ValueError):
            anchor = 0.0
        try:
            score = float(item.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        out.append(
            ReelIdea(
                title=title or quote[:80],
                quote=quote,
                anchor_sec=max(0.0, anchor),
                score=max(0.0, min(100.0, score)),
                reason=str(item.get("reason") or "").strip(),
            )
        )
    return out


def _ideas_similar(a: ReelIdea, b: ReelIdea) -> bool:
    ta = re.sub(r"\s+", " ", a.title.casefold())
    tb = re.sub(r"\s+", " ", b.title.casefold())
    if ta and tb and (ta in tb or tb in ta):
        return True
    if abs(a.anchor_sec - b.anchor_sec) < 25 and ta[:24] == tb[:24]:
        return True
    return False


def _merge_ideas(pool: Sequence[ReelIdea], *, top_n: int) -> List[ReelIdea]:
    ranked = sorted(pool, key=lambda x: x.score, reverse=True)
    out: List[ReelIdea] = []
    for idea in ranked:
        if any(_ideas_similar(idea, x) for x in out):
            continue
        out.append(idea)
        if len(out) >= top_n:
            break
    return out


def _expert_text_for_extract(
    segments: Sequence[SpeechSegment],
    *,
    block_chars: int = 800,
) -> str:
    """Речь Кости блоками с таймкодами [A–B s] — для извлечения идей.

    Здесь вызывался format_expert_blocks_for_prompt(gap_sec=12,
    max_text_per_block=600). Он склеивает реплики, между которыми пауза
    меньше 12 секунд, — в связной речи это весь эфир одним блоком, который
    затем режется до 600 знаков. На часовом эфире экстрактор видел 610
    знаков, то есть 1% записи, и это было приветствие. Все идеи для рилсов
    брались из вступления — отсюда и плоские сценарии.

    Теперь блоки набираются по объёму, а не по паузам: таймкоды остаются
    плотными (модель должна попасть anchor_sec внутрь интервала), а текст
    доходит целиком.
    """
    if not segments:
        return ""
    out: List[str] = []
    buf: List[str] = []
    start = end = 0.0
    size = 0

    def flush() -> None:
        if buf:
            out.append(f"[{int(start)}–{int(end)} s] " + " ".join(buf))

    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        if not buf:
            start = seg.start_sec
        buf.append(text)
        end = seg.end_sec
        size += len(text) + 1
        if size >= block_chars:
            flush()
            buf, size = [], 0
    flush()
    return "\n\n".join(out)


async def _extract_ideas_map_reduce(
    client: Any,
    *,
    title: str,
    kind_label: str,
    segments: Sequence[SpeechSegment],
    model: str,
    max_tokens: int,
) -> List[ReelIdea]:
    blocks = _expert_text_for_extract(segments)
    chunks = _split_expert_blocks(blocks) or [blocks[:_CHUNK_CHARS]]
    all_ideas: List[ReelIdea] = []
    for i, chunk in enumerate(chunks, 1):
        user = (
            f"Тип записи: {kind_label}\n"
            f"Название: {title}\n"
            f"Кусок речи Кости {i}/{len(chunks)}:\n\n"
            f"{chunk}"
        )
        try:
            raw = await _call_openai(
                client,
                model=model,
                system=_SYSTEM_EXTRACT,
                user=user,
                max_tokens=min(3500, max_tokens),
                temperature=0.4,
                response_json=True,
            )
            ideas = _parse_ideas_json(raw)
            logger.info(
                "telemost_reels_brief: extract chunk %s/%s ideas=%s",
                i,
                len(chunks),
                len(ideas),
            )
            all_ideas.extend(ideas)
        except Exception as e:
            logger.warning("telemost_reels_brief: extract chunk %s failed: %s", i, e)
    return _merge_ideas(all_ideas, top_n=_top_ideas_n())


def _window_for_idea(
    segments: Sequence[SpeechSegment],
    idea: ReelIdea,
    *,
    pad_sec: float,
) -> str:
    start = max(0.0, float(idea.anchor_sec) - pad_sec)
    end = float(idea.anchor_sec) + pad_sec
    # если якорь 0 — попробуем найти quote в сегментах
    if idea.anchor_sec <= 0 and idea.quote:
        q = _norm_text(idea.quote)[:40]
        for s in segments:
            if q and q in _norm_text(s.text):
                start = max(0.0, s.start_sec - pad_sec)
                end = s.end_sec + pad_sec
                break
    win = merge_segments_window(list(segments), start, end)
    # Та же ловушка, что была у экстрактора: внутри окна паузы короткие,
    # всё склеивалось в один блок и резалось до 700 знаков. Сценарист видел
    # меньше половины окна, каким бы широким pad ни ставили.
    text = _expert_text_for_extract(win, block_chars=600)
    if text.strip():
        return text
    # fallback: кусок сырого текста вокруг
    return idea.quote or idea.title


# ── Дословность ──────────────────────────────────────────────────────────────

def _norm_text(s: str) -> str:
    t = (s or "").casefold().replace("ё", "е")
    t = re.sub(r"[^\w\s]+", " ", t, flags=re.U)
    return re.sub(r"\s+", " ", t).strip()


def _reel_body(scenario: str) -> str:
    m = re.search(
        r"Текст рилса:\s*(.+?)(?:\n\s*Описание под рилс:|\Z)",
        scenario or "",
        flags=re.S | re.I,
    )
    return (m.group(1) if m else scenario or "").strip()


def _verbatim_hits(scenario: str, window: str, *, min_words: int = 4) -> List[str]:
    body = _norm_text(_reel_body(scenario))
    win = _norm_text(window)
    if not body or not win:
        return []
    words = body.split()
    hits: List[str] = []
    seen: set[str] = set()
    i = 0
    while i < len(words):
        found = None
        for length in range(min(12, len(words) - i), min_words - 1, -1):
            phrase = " ".join(words[i : i + length])
            if len(phrase) < 12:
                continue
            if phrase in win:
                found = phrase
                break
        if found and found not in seen:
            hits.append(found)
            seen.add(found)
            i += max(min_words, len(found.split()))
        else:
            i += 1
    return hits


# ── Сценарий: план → хуки → тела → рубрика ───────────────────────────────────

async def _build_plan(
    client: Any,
    *,
    idea: ReelIdea,
    window: str,
    title: str,
    kind_label: str,
    model: str,
) -> Dict[str, Any]:
    user = (
        f"Тип: {kind_label}\nЭфир: {title}\n\n"
        f"Мысль: {idea.display()}\n"
        f"score={idea.score:.0f}. {idea.reason}\n\n"
        f"Дословное окно речи Кости:\n{window}"
    )
    raw = await _call_openai(
        client,
        model=model,
        system=_SYSTEM_PLAN,
        user=user,
        max_tokens=1200,
        temperature=0.5,
        response_json=True,
    )
    plan = _parse_json_obj(raw)
    phrases = plan.get("must_use_phrases") or []
    if not isinstance(phrases, list) or len(phrases) < 2:
        # fallback из quote
        plan["must_use_phrases"] = [
            p for p in [idea.quote] if p
        ][:3]
    return plan


async def _compete_hooks(
    client: Any,
    *,
    idea: ReelIdea,
    plan: Dict[str, Any],
    window: str,
    model: str,
) -> Tuple[str, List[str]]:
    n = _hook_n()
    user = (
        f"Мысль: {idea.display()}\n"
        f"План: {json.dumps(plan, ensure_ascii=False)}\n\n"
        f"Окно речи:\n{window[:3500]}\n\n"
        f"Дай ровно {n} вариантов первой фразы."
    )
    raw = await _call_openai(
        client,
        model=model,
        system=_SYSTEM_HOOKS,
        user=user,
        max_tokens=900,
        temperature=0.85,
        response_json=True,
    )
    hooks = [
        str(h).strip()
        for h in (_parse_json_obj(raw).get("hooks") or [])
        if str(h).strip()
    ]
    if not hooks:
        hooks = [idea.title]
    if len(hooks) == 1:
        return hooks[0], []
    listed = "\n".join(f"{i}. {h}" for i, h in enumerate(hooks))
    judge_raw = await _call_openai(
        client,
        model=model,
        system=_SYSTEM_JUDGE_HOOKS,
        user=f"Хуки:\n{listed}",
        max_tokens=300,
        temperature=0.2,
        response_json=True,
    )
    idx = int(_parse_json_obj(judge_raw).get("best_index") or 0)
    idx = max(0, min(len(hooks) - 1, idx))
    best = hooks[idx]
    alts = [h for i, h in enumerate(hooks) if i != idx]
    return best, alts


def _few_shot_block(rows: Sequence[Dict[str, Any]]) -> str:
    if not rows:
        return ""
    parts = ["Примеры сценариев, которые у Кости уже заходили (ориентир по тону и структуре):"]
    for i, r in enumerate(rows[:8], 1):
        reach = r.get("reach")
        reach_s = f" · охват≈{reach}" if reach else ""
        body = (r.get("scenario_text") or "").strip()
        if len(body) > 900:
            body = body[:900].rstrip() + "…"
        parts.append(
            f"\n--- пример {i}{reach_s} · {(r.get('idea_title') or '')[:80]} ---\n{body}"
        )
    return "\n".join(parts)


async def _render_one_body(
    client: Any,
    *,
    idea: ReelIdea,
    plan: Dict[str, Any],
    window: str,
    hook: str,
    title: str,
    kind_label: str,
    model: str,
    max_tokens: int,
    few_shot: str = "",
) -> str:
    user = (
        f"Тип: {kind_label}\nЭфир: {title}\n\n"
        f"Мысль: {idea.display()}\n"
        f"Выбранный хук (начни Текст рилса с него): {hook}\n"
        f"План (не цитируй в выводе): {json.dumps(plan, ensure_ascii=False)}\n\n"
        f"Дословное окно:\n{window}\n\n"
        f"Описание проекта:\n{_project_description()}\n"
    )
    if few_shot:
        user += f"\n{few_shot}\n"
    user += "\nСделай готовый текст строго по шаблону."
    return await _call_openai(
        client,
        model=model,
        system=_SYSTEM_RENDER,
        user=user,
        max_tokens=min(3500, max_tokens),
        temperature=0.7,
    )


async def _compete_bodies(
    client: Any,
    *,
    idea: ReelIdea,
    plan: Dict[str, Any],
    window: str,
    hook: str,
    title: str,
    kind_label: str,
    model: str,
    max_tokens: int,
    few_shot: str,
) -> str:
    n = _body_n()
    drafts: List[str] = []
    for _ in range(n):
        drafts.append(
            await _render_one_body(
                client,
                idea=idea,
                plan=plan,
                window=window,
                hook=hook,
                title=title,
                kind_label=kind_label,
                model=model,
                max_tokens=max_tokens,
                few_shot=few_shot,
            )
        )
    if len(drafts) == 1:
        return drafts[0]
    listed = "\n\n-----\n\n".join(
        f"ВАРИАНТ {i}:\n{d}" for i, d in enumerate(drafts)
    )
    judge_raw = await _call_openai(
        client,
        model=model,
        system=_SYSTEM_JUDGE_BODIES,
        user=listed[:12000],
        max_tokens=400,
        temperature=0.2,
        response_json=True,
    )
    idx = int(_parse_json_obj(judge_raw).get("best_index") or 0)
    idx = max(0, min(len(drafts) - 1, idx))
    return drafts[idx]


async def _ensure_verbatim(
    client: Any,
    *,
    draft: str,
    idea: ReelIdea,
    plan: Dict[str, Any],
    window: str,
    hook: str,
    title: str,
    kind_label: str,
    model: str,
    max_tokens: int,
    few_shot: str,
) -> str:
    hits = _verbatim_hits(draft, window)
    if len(hits) >= _VERBATIM_MIN_HITS:
        return draft
    logger.info(
        "telemost_reels_brief: verbatim miss hits=%s — regenerate", len(hits)
    )
    must = plan.get("must_use_phrases") or []
    extra = (
        "ПЕРЕГЕНЕРАЦИЯ: в прошлом варианте почти не было дословных фраз из эфира. "
        "Вставь минимум 2 дословные фразы из must_use_phrases / окна.\n"
        f"must_use_phrases: {json.dumps(must, ensure_ascii=False)}"
    )
    user_plan = dict(plan)
    user_plan["_regen_note"] = extra
    return await _render_one_body(
        client,
        idea=idea,
        plan=user_plan,
        window=window,
        hook=hook,
        title=title,
        kind_label=kind_label,
        model=model,
        max_tokens=max_tokens,
        few_shot=few_shot,
    )


def _rubric_total(scores: Dict[str, Any]) -> float:
    keys = ("hook_3s", "specificity", "kostya_voice", "air_fidelity", "cta_fit")
    vals = []
    for k in keys:
        try:
            vals.append(float(scores.get(k) or 0))
        except (TypeError, ValueError):
            vals.append(0.0)
    return sum(vals)


async def _call_deepseek_rubric(
    *,
    draft: str,
    idea: ReelIdea,
    window: str,
    title: str,
    kind_label: str,
) -> Dict[str, Any]:
    from openai import AsyncOpenAI

    key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY не задан")
    axis_min = _RUBRIC_AXIS_MIN
    system = _SYSTEM_RUBRIC.format(axis_min=axis_min)
    user = (
        f"Тип: {kind_label}\nЭфир: {title}\n\n"
        f"Мысль: {idea.display()}\n\n"
        f"Окно дословной речи:\n{window[:6000]}\n\n"
        f"Проект:\n{_project_description()}\n\n"
        f"Сценарий:\n{draft.strip()}"
    )
    client = AsyncOpenAI(
        api_key=key,
        base_url="https://api.deepseek.com/v1",
        timeout=150.0,
        max_retries=2,
    )
    review_model = _get_review_model()
    resp = await asyncio.wait_for(
        client.chat.completions.create(
            model=review_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.35,
            max_tokens=2000,
        ),
        timeout=120.0,
    )
    try:
        from bot.services.llm_usage_tracker import log_from_response

        await log_from_response(
            resp,
            provider="deepseek",
            model=str(review_model),
            request_kind="reels_rubric",
        )
    except Exception as e:
        logger.debug("reels rubric usage: %s", e)
    choice = resp.choices[0] if resp.choices else None
    text = (
        (getattr(choice.message, "content", None) or "").strip() if choice else ""
    )
    data = _parse_json_obj(text)
    scores = data.get("scores") if isinstance(data.get("scores"), dict) else {}
    total = data.get("total")
    try:
        total_f = float(total)
    except (TypeError, ValueError):
        total_f = _rubric_total(scores)
    edits = data.get("edits") if isinstance(data.get("edits"), list) else []
    # фильтр: правки только если есть слабые оси
    weak = [
        k
        for k, v in scores.items()
        if _safe_float(v) < axis_min
    ]
    if not weak:
        edits = []
    return {"scores": scores, "total": total_f, "edits": [str(e) for e in edits if e]}


def _safe_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


async def _rubric_revise(
    client: Any,
    *,
    draft: str,
    idea: ReelIdea,
    plan: Dict[str, Any],
    window: str,
    hook: str,
    title: str,
    kind_label: str,
    model: str,
    max_tokens: int,
    few_shot: str,
) -> Tuple[str, Dict[str, Any]]:
    if not _crosscheck_enabled() or _review_rounds() <= 0:
        return draft, {}

    best = draft
    best_rubric: Dict[str, Any] = {}
    best_total = -1.0
    current = draft

    for round_i in range(_review_rounds()):
        try:
            rubric = await _call_deepseek_rubric(
                draft=current,
                idea=idea,
                window=window,
                title=title,
                kind_label=kind_label,
            )
        except Exception as e:
            logger.warning("telemost_reels_brief: rubric failed: %s", e)
            return best if best_total >= 0 else draft, best_rubric

        total = float(rubric.get("total") or 0)
        if total > best_total:
            best_total = total
            best = current
            best_rubric = rubric

        edits = rubric.get("edits") or []
        if not edits:
            break

        edit_block = "\n".join(f"— {e}" for e in edits)
        messages = [
            {"role": "system", "content": _SYSTEM_RENDER},
            {
                "role": "user",
                "content": (
                    f"Тип: {kind_label}\nЭфир: {title}\n"
                    f"Мысль: {idea.display()}\nХук: {hook}\n"
                    f"План: {json.dumps(plan, ensure_ascii=False)}\n"
                    f"Окно:\n{window}\n\n{few_shot}"
                ),
            },
            {"role": "assistant", "content": current},
            {
                "role": "user",
                "content": (
                    "Учти правки редактора (только их). Сохрани дословные фразы из эфира. "
                    "Выдай обновлённый сценарий строго по шаблону.\n\n"
                    f"Правки:\n{edit_block}"
                ),
            },
        ]
        revised = await _call_openai_messages(
            client,
            model=model,
            messages=messages,
            max_tokens=min(3500, max_tokens),
            temperature=0.55,
        )
        # принять только если оценка выросла
        try:
            rubric2 = await _call_deepseek_rubric(
                draft=revised,
                idea=idea,
                window=window,
                title=title,
                kind_label=kind_label,
            )
            t2 = float(rubric2.get("total") or 0)
        except Exception:
            break
        if t2 > best_total:
            best = revised
            best_total = t2
            best_rubric = rubric2
            current = revised
            logger.info(
                "telemost_reels_brief: rubric round %s accepted total=%.1f",
                round_i + 1,
                t2,
            )
        else:
            logger.info(
                "telemost_reels_brief: rubric round %s rejected (%.1f ≤ %.1f)",
                round_i + 1,
                t2,
                best_total,
            )
            break
    return best, best_rubric


async def _build_scenario_bundle(
    client: Any,
    *,
    idea: ReelIdea,
    segments: Sequence[SpeechSegment],
    title: str,
    kind_label: str,
    model: str,
    max_tokens: int,
    few_shot_rows: Sequence[Dict[str, Any]],
) -> ScenarioBundle:
    window = _window_for_idea(segments, idea, pad_sec=_window_pad())
    few_shot = _few_shot_block(few_shot_rows)
    plan = await _build_plan(
        client,
        idea=idea,
        window=window,
        title=title,
        kind_label=kind_label,
        model=model,
    )
    hook, alts = await _compete_hooks(
        client, idea=idea, plan=plan, window=window, model=model
    )
    draft = await _compete_bodies(
        client,
        idea=idea,
        plan=plan,
        window=window,
        hook=hook,
        title=title,
        kind_label=kind_label,
        model=model,
        max_tokens=max_tokens,
        few_shot=few_shot,
    )
    draft = await _ensure_verbatim(
        client,
        draft=draft,
        idea=idea,
        plan=plan,
        window=window,
        hook=hook,
        title=title,
        kind_label=kind_label,
        model=model,
        max_tokens=max_tokens,
        few_shot=few_shot,
    )
    final, rubric = await _rubric_revise(
        client,
        draft=draft,
        idea=idea,
        plan=plan,
        window=window,
        hook=hook,
        title=title,
        kind_label=kind_label,
        model=model,
        max_tokens=max_tokens,
        few_shot=few_shot,
    )
    return ScenarioBundle(
        text=final.strip(),
        plan=plan,
        hooks_alt=alts,
        rubric=rubric,
        window=window,
        idea=idea,
    )


def _chunks(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    out: list[str] = []
    rest = raw
    while rest:
        if len(rest) <= _TG_CHUNK:
            out.append(rest)
            break
        cut = rest.rfind("\n", 0, _TG_CHUNK)
        if cut < 800:
            cut = _TG_CHUNK
        out.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return out


def _format_scenario_message(idx: int, bundle: ScenarioBundle) -> str:
    idea = bundle.idea
    head = f"#{idx}"
    if idea:
        head += f" · {idea.title[:100]} (score {idea.score:.0f})"
        if idea.anchor_sec:
            m = int(idea.anchor_sec) // 60
            s = int(idea.anchor_sec) % 60
            head += f" · @{m}:{s:02d}"
    parts = [head, "", bundle.text.strip()]
    if bundle.hooks_alt:
        alts = "\n".join(f"· {h}" for h in bundle.hooks_alt[:6])
        parts.append("")
        parts.append("Альтернативные хуки:")
        parts.append(alts)
    hits = _verbatim_hits(bundle.text, bundle.window)
    if hits:
        parts.append("")
        parts.append("Дословные опоры: " + " | ".join(hits[:3]))
    return "\n".join(parts)


# ── Оркестратор ──────────────────────────────────────────────────────────────

async def _run_reels_brief(
    bot_app: Any,
    pending_id: uuid.UUID,
    row: Dict[str, Any],
    meta: Dict[str, Any],
    *,
    recording_kind: str,
) -> None:
    from openai import AsyncOpenAI

    pid = str(pending_id)
    chat_id, topic_id = _target_topic()
    bot = getattr(bot_app, "bot", None)
    storage = getattr(bot_app, "user_storage", None)
    title = (
        meta.get("topic_title")
        or meta.get("source")
        or row.get("subject")
        or "Эфир"
    )
    kind_label = KIND_LABELS.get(recording_kind, "Эфир")
    transcript = (row.get("transcript_text") or "").strip()

    try:
        if not bot or not chat_id or not topic_id:
            logger.warning("telemost_reels_brief: no chat/bot/topic")
            return
        if len(transcript) < 200:
            logger.warning(
                "telemost_reels_brief: transcript too short pending=%s", pid
            )
            return

        key = (config.OPENAI_API_KEY or "").strip()
        if not key:
            raise RuntimeError("OPENAI_API_KEY не задан")
        model = _get_openai_model()
        max_tokens = _get_max_tokens()
        client = AsyncOpenAI(api_key=key, timeout=240.0, max_retries=1)
        kwargs = {"message_thread_id": int(topic_id)}
        title_str = str(title)

        segments = parse_expert_segments(transcript, _speaker_names())
        if not segments:
            logger.warning(
                "telemost_reels_brief: no expert segments pending=%s — fallback raw",
                pid,
            )

        ideas = await _extract_ideas_map_reduce(
            client,
            title=title_str,
            kind_label=kind_label,
            segments=segments
            or [
                SpeechSegment(0.0, transcript[:8000], float(len(transcript) / 14.0))
            ],
            model=model,
            max_tokens=max_tokens,
        )
        if not ideas:
            await bot.send_message(
                chat_id,
                f"🎬 Reels: не нашёл сильных мыслей в «{title_str}».",
                **kwargs,
            )
            return

        few_shot_rows: List[Dict[str, Any]] = []
        if storage and hasattr(storage, "list_top_reels_few_shot"):
            try:
                few_shot_rows = await storage.list_top_reels_few_shot(8)
            except Exception as e:
                logger.debug("few-shot load: %s", e)

        header = (
            f"🎬 <b>Reels-сценарии</b> · {kind_label}: {title_str}\n"
            f"(топ {len(ideas)} мыслей из эфира, с дословными окнами)"
        )
        await bot.send_message(chat_id, header, parse_mode="HTML", **kwargs)
        await asyncio.sleep(0.4)

        for idx, idea in enumerate(ideas, 1):
            try:
                bundle = await _build_scenario_bundle(
                    client,
                    idea=idea,
                    segments=segments
                    or [
                        SpeechSegment(
                            0.0, transcript[:12000], float(len(transcript) / 14.0)
                        )
                    ],
                    title=title_str,
                    kind_label=kind_label,
                    model=model,
                    max_tokens=max_tokens,
                    few_shot_rows=few_shot_rows,
                )
            except Exception as e:
                logger.exception(
                    "telemost_reels_brief: scenario failed idea=%r: %s",
                    idea.title,
                    e,
                )
                await bot.send_message(
                    chat_id,
                    f"#{idx} · {idea.title[:100]}\n\n⛔ Ошибка генерации: {e}",
                    **kwargs,
                )
                continue

            full_text = _format_scenario_message(idx, bundle)
            ref = _new_ref_code()
            scenario_id: Optional[uuid.UUID] = None
            if storage and hasattr(storage, "insert_reels_scenario"):
                try:
                    scenario_id = await storage.insert_reels_scenario(
                        pending_id=pending_id
                        if isinstance(pending_id, uuid.UUID)
                        else None,
                        ref_code=ref,
                        air_title=title_str,
                        idea_title=idea.title,
                        idea_score=idea.score,
                        anchor_sec=idea.anchor_sec,
                        scenario_text=bundle.text,
                        transcript_window=bundle.window,
                        hook_alternatives="\n".join(bundle.hooks_alt),
                        plan_json=bundle.plan,
                        rubric_json=bundle.rubric,
                        chat_id=chat_id,
                    )
                except Exception as e:
                    logger.warning("insert_reels_scenario: %s", e)

            parts = _chunks(full_text)
            first_msg_id = 0
            for pi, part in enumerate(parts):
                kb = None
                if pi == len(parts) - 1 and scenario_id:
                    kb = feedback_keyboard(scenario_id)
                msg = await bot.send_message(
                    chat_id,
                    part[:4096],
                    reply_markup=kb,
                    **kwargs,
                )
                if pi == 0:
                    first_msg_id = int(getattr(msg, "message_id", 0) or 0)
                await asyncio.sleep(0.35)

            if (
                scenario_id
                and storage
                and hasattr(storage, "update_reels_scenario_message")
                and first_msg_id
            ):
                await storage.update_reels_scenario_message(
                    scenario_id, chat_id=chat_id, message_id=first_msg_id
                )
            await asyncio.sleep(0.5)

        logger.info(
            "telemost_reels_brief sent pending=%s kind=%s ideas=%d",
            pid,
            recording_kind,
            len(ideas),
        )
    except Exception as e:
        logger.exception("telemost_reels_brief failed pending=%s: %s", pid, e)
    finally:
        _active.discard(pid)
