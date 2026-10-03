"""После индексации телемоста: чат в Контент заводе под сценарий Reels.

Вместо генерации 5 вариантов в TG-топик:
  1. Создаём web-чат (format=reels) с выбранным объектом эфира.
  2. Кладём задание «напиши сценарий… по мотивам этого эфира».
  3. В прежний TG-топик — короткая ссылка на студию ``?chat=<id>``.

Ниже в файле остаются хелперы ручной/старой генерации сценариев (few-shot, рубрика),
если ими пользуются другие команды.
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

_SYSTEM_DISTILL = """Ты — аналитик. Твоя работа — выпарить из куска эфира ОДНУ мысль.

Тебе дают дословную речь Кости. В ней мысль живёт в своём контексте: своя
аудитория, свой повод, свои примеры, свой порядок изложения. Всё это — упаковка
того разговора. Тебе нужно то, что останется, если упаковку убрать.

ЧТО ВЫПАРИТЬ.
insight — одно утверждение о том, как устроена жизнь. Не «Костя говорит, что
надо прощать», а само утверждение: «непрощение бьёт не по тому, кого ты не
простил, а по твоим детям».
  - Никаких примеров и историй — ни Костиных, ни своих.
  - Никаких упоминаний эфира, клуба, участников, вопроса из зала.
  - Не «важно помнить, что…» — это не утверждение, а вода.
  - Проверка: это можно сказать незнакомому человеку на улице, и он поймёт.

common_belief — как человек думает по умолчанию, то есть ровно то, чему insight
противоречит. Если противоречия нет, мысль для рилса слабая — так и напиши.

stakes — чем незнание оборачивается в обычной жизни. Конкретно: что ломается,
где болит, что человек делает не так, сам того не замечая.

voice — 3–6 характерных оборотов Кости из этого куска. Это СЛОВАРЬ ИНТОНАЦИИ, а
не цитаты для вставки: как он строит фразу, какими словами называет вещи. Коротко.

Верни ТОЛЬКО JSON:
{
  "insight": "...",
  "common_belief": "...",
  "stakes": "...",
  "voice": ["...", "..."]
}
Пиши по-русски."""

_SYSTEM_PLAN = """Ты — режиссёр коротких видео. НЕ пиши готовый текст рилса.

Тебе дают ОДНУ мысль — уже очищенную от эфира. Расшифровки у тебя нет: она не
нужна, из неё всё взяли. Твоя работа — придумать, как эту мысль показать за
минуту человеку, который тебя не знает.

scene — САМОЕ ВАЖНОЕ ПОЛЕ. Одна конкретная бытовая сцена, в которой зритель
узнает себя. Её ты придумываешь сам, под мысль. Не «человек испытывает обиду»,
а «ты листаешь ленту, видишь его фото — и большой палец сам останавливается».
Сцена должна быть массовой: её проживали десятки тысяч людей, а не один
участник клуба. Проверка: в ней есть что увидеть глазами.

Тип хука выбери один: accusation | confession | direct_question | paradox.

Верни ТОЛЬКО JSON:
{
  "hook_type": "paradox",
  "scene": "конкретная бытовая сцена для зрителя",
  "first_3_sec_promise": "что зритель получает в первые 3 секунды",
  "tension": "где напряжение",
  "turn": "где поворот — что переворачивает привычное понимание",
  "loop_close": "чем закрывается петля",
  "cta": "club|bot|channel|soft_none"
}
Пиши по-русски в значениях JSON."""

_SYSTEM_HOOKS = """Ты копирайтер Reels. Дай варианты ТОЛЬКО первой фразы (хук на 3 секунды).

Каждая строка — отдельный хук, 5–14 слов, разговорный голос.
Хук открывает петлю: называет то, что человек прячет, или говорит то, с чем он
мгновенно не согласен. «Сегодня поговорим о прощении» — петля не открыта.
Без таймингов, без пояснений.

Верни ТОЛЬКО JSON:
{"hooks":["...","..."]}"""

_SYSTEM_JUDGE_HOOKS = """Выбери лучший хук для досмотра первых 3 секунд.
Верни ТОЛЬКО JSON: {"best_index":0,"reason":"..."}"""

_SYSTEM_RENDER = """Ты — сценарист Reels. Ниша: духовное развитие. Пишешь текст, который человек досматривает до конца и пересылает.

ЧТО У ТЕБЯ НА РУКАХ — И ЧЕГО НЕТ.
У тебя есть одна мысль и план. Расшифровки эфира нет, и это сделано нарочно.

Эфир — сырьё, из него уже всё взяли. Там мысль жила в своём контексте: своя
аудитория, свой повод, свои примеры, свой порядок изложения. В рилс переносится
только смысл; всё остальное было упаковкой того разговора.

Это не пересказ, это другая работа. Из нефти делают бензин: состав меняется,
свойство остаётся. Если на выходе получилась та же последовательность, в которой
рассуждал Костя, только другими словами, — работа не сделана.

ПРИМЕР В РИЛСЕ — ТВОЙ.
В плане есть scene — конкретная бытовая сцена, написанная для зрителя.
Разворачивай её. Примеры и истории из эфира сюда не переносятся: они были нужны
тем людям в том разговоре, а твой зритель их не слышал и контекста не имеет.
Если в тексте появилось «один человек рассказывал» или «мне тут написали» —
ты вернулся к пересказу.

ГОЛОС.
В поле voice — обороты, как говорит Костя. Это СЛОВАРЬ ИНТОНАЦИИ: по нему ты
сверяешь тон, а не берёшь текст. Вставлять эти обороты в сценарий нельзя — они
выдернуты из живой речи, бывают оборванными и с ошибками распознавания, и в
готовом тексте читаются как сбой («вылази из мозгов»).

Голос — это короткие предложения, прямое «ты», отсутствие церковного канцелярита,
спокойная прямота без нажима и без придыхания.

ДЛИНА — СКОЛЬКО НУЖНО МЫСЛИ.
Считать слова не надо. Правило одно: ни одного лишнего слова. Если фразу можно
убрать и ничего не потеряется — убери. Сильная мысль на сорок слов лучше
растянутой на полтораста.

ЧТО ДЕЛАЕТ РИЛС ВИРУСНЫМ. Это главное, остальное — детали.

Первые три секунды решают всё. Хук должен открыть петлю, которую невозможно
оставить незакрытой: назвать то, что человек прячет, или сказать то, с чем он
мгновенно не согласен.

Дальше напряжение только растёт. Не объясняй сразу — сначала сделай больно
узнаванием. Человек должен подумать «откуда он про меня знает».

Поворот должен быть неочевидным. Если вывод можно было предсказать с первой
секунды, рилс не перешлют. Переворачивай привычное: не «надо прощать», а почему
непрощение бьёт по деньгам и детям.

Финальная фраза — то, что человек процитирует другу. Ради неё и пересылают.
Она должна работать отдельно от всего остального.

Говори с одним человеком, а не с аудиторией. «Ты», а не «мы» и не «друзья».

СТРОЕНИЕ ТЕКСТА.
1. Первая фраза — выбранный хук, слово в слово.
2. Сцена из плана: разверни её так, чтобы зритель увидел себя.
3. Поворот: то, чего он не ждал. Здесь и живёт сама мысль.
4. Финал: фраза, которую уносят с собой. Закрытая мысль, без «подписывайтесь».

НЕ ПОВТОРЯЙСЯ. Одна и та же мысль дважды съедает секунды, которых и так мало.

ЯЗЫК.
Короткие предложения. Обращение на «ты». Никакой воды и общих слов вроде
«важно помнить». Конкретика: не «трудности», а то, что реально происходит
с человеком.

ЗАПРЕЩЕНО в выводе:
- тайминги, таймкоды, «хук на 3 секунде», «захват внимания»;
- указания на паузы, интонации, планы, музыку, монтаж;
- объяснения структуры и любые пометки от себя;
- ссылки на эфир: «как говорил Костя», «в эфире разбирали», «один человек»;
- «мы», «у нас», «на нашем языке» — зритель пока не внутри, он никого не знает;
- кривые обороты из voice, вставленные дословно.

ОПИСАНИЯ ПОД РИЛС — ровно ТРИ варианта, все три обязательны, по 1–2 предложения.
Каждый ведёт в своё место и цепляется за тему ролика, а не за общие слова.
Схлопывать их в один нельзя.

CTA живёт только в описаниях. В «Тексте рилса» никаких «полный эфир в клубе»
и «подписывайся» — там только сама мысль.

Формат — СТРОГО:

Обложка: [3–5 слов, крючок]

Текст рилса: [текст]

Описание под рилс:
— Вариант 1 (на клуб): [текст]
— Вариант 2 (на библейского бота): [текст]
— Вариант 3 (на телеграм-канал): [текст]

Пиши по-русски."""

_SYSTEM_JUDGE_BODIES = """Сравни варианты сценария Reels. Выбери лучший по:
сила первых 3 сек / конкретность / голос Кости / опора на эфир / уместный CTA.
Верни ТОЛЬКО JSON: {"best_index":0,"reason":"..."}"""

_SYSTEM_RUBRIC = """Ты — редактор коротких видео. Перекрёстная проверка сценария Reels.

Сценарий написан ПО МЫСЛИ, а не по расшифровке. Дословных цитат из эфира тут
быть не должно — это не недостаток, а требование.

Оцени по 6 осям 0..10:
- hook_3s — сила первых 3 секунд;
- specificity — конкретность vs вода;
- kostya_voice — голос Кости (живой, не ChatGPT);
- idea_fidelity — мысль передана без искажения и не выхолощена до банальности;
- own_scene — сцена написана для зрителя. Снижай балл за пересказ эфира:
  «один человек рассказывал», «мне написали», истории про участников клуба,
  ссылки на разговор, которого зритель не слышал;
- cta_fit — уместность CTA.

Правки (edits) — ТОЛЬКО по осям строго ниже порога {axis_min}. Если все оси ≥ порога — edits=[].
Не переписывай сценарий целиком.

Верни ТОЛЬКО JSON:
{{
  "scores": {{"hook_3s":0,"specificity":0,"kostya_voice":0,"idea_fidelity":0,"own_scene":0,"cta_fit":0}},
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
    distilled: Dict[str, Any] = field(default_factory=dict)


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


def _fake_segments(transcript: str, *, chars_per_sec: float = 14.0) -> List[SpeechSegment]:
    """Запасной разбор, когда речь не размечена по говорящим.

    Раньше весь эфир заворачивали в ОДИН SpeechSegment с обрезкой до 8–12 тысяч
    знаков — на часовой записи это опять было только её начало. И окно вокруг
    мысли не работало: сегмент один, границ внутри нет.

    Теперь текст режется на куски по словам, а время оценивается по скорости
    речи. Таймкоды приблизительные, но окна и якоря снова имеют смысл, и ничего
    не выбрасывается.
    """
    text = (transcript or "").strip()
    if not text:
        return []
    size = 1000
    out: List[SpeechSegment] = []
    pos = 0
    while pos < len(text):
        end = min(len(text), pos + size)
        if end < len(text):
            sp = text.rfind(" ", pos + size // 2, end)
            if sp > pos:
                end = sp
        out.append(
            SpeechSegment(pos / chars_per_sec, text[pos:end].strip(), end / chars_per_sec)
        )
        pos = end
    return [seg for seg in out if seg.text]


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


# ── Сценарий: план → хуки → тела → рубрика ───────────────────────────────────

async def _distill_idea(
    client: Any,
    *,
    idea: ReelIdea,
    window: str,
    model: str,
) -> Dict[str, Any]:
    """Шаг «нефть → сырьё для бензина»: из куска эфира — одна голая мысль.

    Нужен, потому что без него сценарий получался пересказом. Раньше окно
    расшифровки лежало перед моделью на каждом шаге — план, хуки, тело, правка —
    и она неизбежно тащила за собой Костину последовательность: его посылка, его
    пример, его вывод. Другими словами, но то же самое.

    Здесь мысль один раз отделяется от разговора, в котором прозвучала, и дальше
    окно не передаётся никуда. Сценарий пишется от утверждения, а сцена для него
    придумывается своя.
    """
    user = (
        f"Мысль, которую надо выпарить: {idea.display()}\n"
        f"Почему её выбрали: {idea.reason}\n\n"
        f"Дословная речь Кости:\n{window}"
    )
    raw = await _call_openai(
        client,
        model=model,
        system=_SYSTEM_DISTILL,
        user=user,
        max_tokens=1200,
        temperature=0.4,
        response_json=True,
    )
    dist = _parse_json_obj(raw)
    insight = str(dist.get("insight") or "").strip()
    if not insight:
        # Без утверждения писать не от чего — берём заголовок мысли как есть.
        logger.warning("telemost_reels_brief: дистилляция пустая, идея=%r", idea.title)
        dist["insight"] = idea.display()
    voice = dist.get("voice")
    if not isinstance(voice, list):
        dist["voice"] = []
    return dist


def _distilled_block(dist: Dict[str, Any]) -> str:
    """Мысль для всех шагов после дистилляции. Расшифровки тут нет намеренно."""
    lines = [f"МЫСЛЬ: {dist.get('insight') or ''}"]
    if dist.get("common_belief"):
        lines.append(f"Как думают обычно: {dist['common_belief']}")
    if dist.get("stakes"):
        lines.append(f"Чем оборачивается: {dist['stakes']}")
    voice = [str(v).strip() for v in (dist.get("voice") or []) if str(v).strip()]
    if voice:
        lines.append("Словарь интонации Кости (не цитаты): " + " / ".join(voice[:6]))
    return "\n".join(lines)


async def _build_plan(
    client: Any,
    *,
    dist: Dict[str, Any],
    title: str,
    kind_label: str,
    model: str,
) -> Dict[str, Any]:
    user = (
        f"Тип записи-источника: {kind_label}\n\n"
        f"{_distilled_block(dist)}\n\n"
        "Придумай сцену и план рилса."
    )
    raw = await _call_openai(
        client,
        model=model,
        system=_SYSTEM_PLAN,
        user=user,
        max_tokens=1200,
        temperature=0.6,
        response_json=True,
    )
    plan = _parse_json_obj(raw)
    if not str(plan.get("scene") or "").strip():
        logger.warning("telemost_reels_brief: план без сцены — рилс будет абстрактным")
    return plan


async def _compete_hooks(
    client: Any,
    *,
    dist: Dict[str, Any],
    plan: Dict[str, Any],
    model: str,
) -> Tuple[str, List[str]]:
    n = _hook_n()
    user = (
        f"{_distilled_block(dist)}\n\n"
        f"План: {json.dumps(plan, ensure_ascii=False)}\n\n"
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
        hooks = [str(dist.get("insight") or "").strip() or "Ты и сам это знаешь."]
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
    dist: Dict[str, Any],
    plan: Dict[str, Any],
    hook: str,
    model: str,
    max_tokens: int,
    few_shot: str = "",
) -> str:
    # Окно расшифровки сюда не передаётся. Это главное изменение: пока оно лежало
    # перед моделью, она переписывала эфир своими словами вместо того, чтобы
    # собрать рилс заново из одной мысли.
    user = (
        f"{_distilled_block(dist)}\n\n"
        f"Выбранный хук (начни Текст рилса с него): {hook}\n"
        f"План (не цитируй в выводе): {json.dumps(plan, ensure_ascii=False)}\n\n"
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
        temperature=0.8,
    )


async def _compete_bodies(
    client: Any,
    *,
    dist: Dict[str, Any],
    plan: Dict[str, Any],
    hook: str,
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
                dist=dist,
                plan=plan,
                hook=hook,
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
        user=listed,
        max_tokens=400,
        temperature=0.2,
        response_json=True,
    )
    idx = int(_parse_json_obj(judge_raw).get("best_index") or 0)
    idx = max(0, min(len(drafts) - 1, idx))
    return drafts[idx]


# _ensure_verbatim удалён намеренно.
#
# Он перегенерировал сценарий, пока в нём не наберётся две дословные фразы из
# эфира (_VERBATIM_MIN_HITS). То есть копирование исходника было не побочным
# эффектом, а требованием, которое код проверял и добивался. Вместе с
# must_use_phrases в плане и осью air_fidelity в рубрике это держало сценарий
# пересказом: три механизма из разных мест тянули текст обратно в эфир.
#
# Теперь точность проверяется по смыслу — ось idea_fidelity, — а дословность
# не требуется и не поощряется.


def _rubric_total(scores: Dict[str, Any]) -> float:
    keys = (
        "hook_3s",
        "specificity",
        "kostya_voice",
        "idea_fidelity",
        "own_scene",
        "cta_fit",
    )
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
    dist: Dict[str, Any],
    title: str,
    kind_label: str,
) -> Dict[str, Any]:
    from openai import AsyncOpenAI

    key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY не задан")
    axis_min = _RUBRIC_AXIS_MIN
    system = _SYSTEM_RUBRIC.format(axis_min=axis_min)
    # Расшифровку рецензенту не даём: он сверяет сценарий с мыслью, а не с
    # исходником. Пока окно сюда приходило, ось «опора на эфир» вознаграждала
    # как раз то, от чего уходим.
    user = (
        f"Тип записи-источника: {kind_label}\nЭфир: {title}\n\n"
        f"{_distilled_block(dist)}\n\n"
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
    dist: Dict[str, Any],
    plan: Dict[str, Any],
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
                dist=dist,
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
                    f"Тип записи-источника: {kind_label}\n"
                    f"{_distilled_block(dist)}\nХук: {hook}\n"
                    f"План: {json.dumps(plan, ensure_ascii=False)}\n\n{few_shot}"
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
                dist=dist,
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


def _tidy_output(text: str) -> str:
    """Хвостовые пробелы в конце строк — модель ставит их как markdown-перенос."""
    return "\n".join(line.rstrip() for line in (text or "").strip().splitlines())


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

    # Единственный шаг, который видит расшифровку. Дальше работаем с мыслью.
    dist = await _distill_idea(client, idea=idea, window=window, model=model)
    logger.info(
        "telemost_reels_brief: мысль выпарена — %s",
        str(dist.get("insight") or "")[:160],
    )

    plan = await _build_plan(
        client, dist=dist, title=title, kind_label=kind_label, model=model
    )
    hook, alts = await _compete_hooks(client, dist=dist, plan=plan, model=model)
    draft = await _compete_bodies(
        client,
        dist=dist,
        plan=plan,
        hook=hook,
        model=model,
        max_tokens=max_tokens,
        few_shot=few_shot,
    )
    final, rubric = await _rubric_revise(
        client,
        draft=draft,
        dist=dist,
        plan=plan,
        hook=hook,
        title=title,
        kind_label=kind_label,
        model=model,
        max_tokens=max_tokens,
        few_shot=few_shot,
    )
    return ScenarioBundle(
        text=_tidy_output(final),
        plan=plan,
        hooks_alt=alts,
        rubric=rubric,
        window=window,
        idea=idea,
        distilled=dist,
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
    insight = str((bundle.distilled or {}).get("insight") or "").strip()
    if insight:
        parts.append("")
        parts.append(f"Мысль в основе: {insight}")
    return "\n".join(parts)


# ── Оркестратор ──────────────────────────────────────────────────────────────

_PROMPT_REELS = "напиши сценарий для вирусного reels. по мотивам этого эфира"


def _source_label(meta: Dict[str, Any], row: Dict[str, Any], title: str) -> str:
    """Тот же source, что уходит в Chroma при индексации (до 80 символов)."""
    raw = (
        str(meta.get("source") or "").strip()
        or str(title or "").strip()
        or str(row.get("subject") or "").strip()
        or "Телемост"
    )
    return raw[:80]


def _facet_for_kind(recording_kind: str) -> str:
    kind = (recording_kind or "").strip().lower()
    if kind in ("efir", "molitva", "pokayanie", "qa"):
        return kind
    return "efir"


def _studio_chat_url(chat_id: uuid.UUID) -> str:
    from bot.features.content_factory_feature import factory_web_url

    base = factory_web_url().rstrip("/")
    if not base:
        return ""
    return f"{base}/?chat={chat_id}"


async def _create_reels_studio_chat(
    bot_app: Any,
    *,
    title: str,
    source: str,
    recording_kind: str,
    prompt: str,
) -> Optional[uuid.UUID]:
    """Создать чат студии: format=reels, объект эфира, задание в истории."""
    storage = getattr(bot_app, "user_storage", None)
    if storage is None or not hasattr(storage, "create_web_chat"):
        logger.warning("telemost_reels_brief: нет user_storage / create_web_chat")
        return None

    from course.products import active_product_id
    from course.stories_cycle import load_stages, normalize_stage
    from web.objects import build_tree, material_id

    facet = _facet_for_kind(recording_kind)
    mat_id = material_id(facet, source)
    try:
        await build_tree(bot_app)
    except Exception as e:
        logger.warning("telemost_reels_brief: build_tree: %s", e)

    pid = active_product_id()
    stages = await load_stages(storage)
    stage_raw = await storage.get_content_setting(pid, "stories_cycle_stage")
    stage = normalize_stage(
        stage_raw if isinstance(stage_raw, str) else "", stages
    )
    focus_raw = await storage.get_content_setting(pid, "focus")
    focus = str(focus_raw or "") if isinstance(focus_raw, str) else ""
    created_by = int(getattr(config, "SUPER_ADMIN_ID", 0) or 0) or None

    chat_title = f"Reels · {title}"[:200]
    cid = await storage.create_web_chat(
        product_id=pid,
        title=chat_title,
        format="reels",
        stage=stage,
        focus=focus,
        context={"objects": [mat_id]},
        created_by=created_by,
    )
    if not cid:
        return None
    await storage.insert_web_chat_message(
        chat_id=cid,
        role="user",
        text=prompt,
    )
    logger.info(
        "telemost_reels_brief: studio chat %s mat=%s source=%r",
        cid,
        mat_id,
        source[:60],
    )
    return cid


async def _run_reels_brief(
    bot_app: Any,
    pending_id: uuid.UUID,
    row: Dict[str, Any],
    meta: Dict[str, Any],
    *,
    recording_kind: str,
) -> None:
    pid = str(pending_id)
    chat_id, topic_id = _target_topic()
    bot = getattr(bot_app, "bot", None)
    title = (
        meta.get("topic_title")
        or meta.get("source")
        or row.get("subject")
        or "Эфир"
    )
    title_str = str(title).strip() or "Эфир"
    source = _source_label(meta if isinstance(meta, dict) else {}, row, title_str)
    prompt = f"{_PROMPT_REELS} «{title_str}»"

    try:
        if not bot or not chat_id or not topic_id:
            logger.warning("telemost_reels_brief: no chat/bot/topic")
            return

        studio_id = await _create_reels_studio_chat(
            bot_app,
            title=title_str,
            source=source,
            recording_kind=recording_kind,
            prompt=prompt,
        )
        url = _studio_chat_url(studio_id) if studio_id else ""
        kwargs = {"message_thread_id": int(topic_id)}

        if not studio_id or not url:
            await bot.send_message(
                chat_id,
                f"🎬 Не удалось создать чат студии для «{title_str}». "
                f"Проверьте WEB_ENABLED / WEB_DOMAIN.",
                **kwargs,
            )
            return

        text = (
            f"готов новый сценарий reels по мотивам «{title_str}»\n\n"
            f"{url}"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Открыть в Контент заводе",
                        url=url,
                    )
                ]
            ]
        )
        await bot.send_message(
            chat_id,
            text[:4000],
            reply_markup=kb,
            disable_web_page_preview=False,
            **kwargs,
        )
        logger.info(
            "telemost_reels_brief studio link pending=%s kind=%s chat=%s",
            pid,
            recording_kind,
            studio_id,
        )
    except Exception as e:
        logger.exception("telemost_reels_brief failed pending=%s: %s", pid, e)
    finally:
        _active.discard(pid)
