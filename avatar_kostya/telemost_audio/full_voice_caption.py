"""LLM: название + тизер полной записи.

Два прохода. Сначала «карта записи»: модель читает расшифровку ЦЕЛИКОМ,
кусками, и выписывает, какие самостоятельные темы в ней разобраны. Потом
по этой карте пишется название и тизер.

Зачем так. Записи разные: иногда это одна сквозная мысль, иногда разбор
четырёх не связанных между собой ситуаций. Раньше форму записи задавал тип
письма, а копирайтеру было велено найти «главную мысль» и запрещено
перечислять темы. На разборе из четырёх ситуаций это давало ровно то, за что
ругали: первая расписана, остальные упомянуты вскользь.

Теперь форму определяет сама модель, прочитав всю запись, и от формы зависит,
как устроены название и тизер.
"""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any, Dict, List

from telemost_audio.recording_kind import (
    KIND_EFIR,
    KIND_LABELS,
    KIND_MOLITVA,
    KIND_POKAYANIE,
    KIND_QA,
)

logger = logging.getLogger(__name__)

# Фразы-паразиты в начале тизера (черновиковый тон) — для всех типов записей.
_PARASITE_RE = re.compile(
    r"^\s*("
    r"в этой молитве\b|"
    r"в этом эфире\b|"
    r"в данном эфире\b|"
    r"в этой записи\b|"
    r"в данном (эфире|выпуске|разговоре)\b|"
    r"в этой (встрече|беседе)\b|"
    r"константин (нам |здесь )?(предлагает|говорит|разбирает)\b|"
    r"костя (нам |здесь )?(предлагает|говорит|разбирает)\b|"
    r"сегодня (мы|константин|костя)\b|"
    r"эта (молитва|запись|встреча) (представляет собой|является|о том)\b"
    r").*?[.!?]\s*",
    re.IGNORECASE | re.DOTALL,
)

_MECHANICAL_PREFIX_RE = re.compile(
    r"(?i)^(эфир вопрос и ответы|эфир|молитва|покаяние)\s*[.:—–-]\s*"
)

# ── Проход 1: карта записи ───────────────────────────────────────────────────

_SYSTEM_MAP = """Ты аналитик. Тебе дают КУСОК расшифровки одной записи.

Выпиши САМОСТОЯТЕЛЬНЫЕ темы, которые в этом куске разобраны. Самостоятельная —
значит человек, которого волнует именно она, получил здесь ответ, и остальная
запись ему для этого не нужна.

Не выписывай: приветствия, оргвопросы,техническую часть, переходы между темами,
короткие реплики без разбора.

Если весь кусок — это развитие одной темы, верни одну тему. Не дроби.

Верни ТОЛЬКО JSON:
{"topics":[{"title":"тема в 2-5 словах","pain":"состояние человека, которому это про него","gist":"суть разбора в одном предложении"}]}"""

_SYSTEM_MAP_REDUCE = """Тебе дают темы, выписанные из разных кусков ОДНОЙ записи.

Сделай три вещи.

1. Склей дубли. Одна тема, описанная разными словами, — это одна тема.
   Тема, которая просто развивает предыдущую, — тоже она.

2. Определи форму записи:
   - "one_line" — вся запись ведёт ОДНУ сквозную мысль. Темы — её грани,
     по отдельности они не живут.
   - "collection" — в записи НЕСКОЛЬКО самостоятельных разборов. Разные
     ситуации, разные люди, разные ответы. Одну на другую не заменить.
   Если после склейки осталась одна-две тесно связанные темы — это one_line.
   Если три и больше независимых — collection.

3. Сформулируй core — то общее, ради чего эту запись стоит слушать, одной фразой.
   Для collection это не первая тема из списка, а то, что связывает все.

Темы верни в том порядке, в каком они идут в записи. Не выдумывай новых.
Сначала самые весомые: те, на которые в записи ушло больше всего времени и
которые задевают больше людей. Мелкие и частные ставь в конец — до подписи
дойдут не все.

Верни ТОЛЬКО JSON:
{"shape":"one_line|collection","core":"...","topics":[{"title":"...","pain":"..."}]}"""


# ── Проход 2: копирайтер ─────────────────────────────────────────────────────

_STRUCTURE_ONE_LINE = """ФОРМА ЗАПИСИ: СКВОЗНАЯ МЫСЛЬ.
Вся запись ведёт одну мысль, она указана в карте как core.

НАЗВАНИЕ — художественное, к этой мысли.
ТИЗЕР — ровно 2–4 предложения, по порядку:
 – узнавание: конкретное состояние, а не «трудности»;
 – почему привычный выход не работает;
 – что откроется, если дослушать. Без спойлера.
Темы списком не перечисляй: тема здесь одна."""

_STRUCTURE_COLLECTION = """ФОРМА ЗАПИСИ: СБОРНИК.
В записи несколько САМОСТОЯТЕЛЬНЫХ разборов — они перечислены в карте.
Человек приходит за своим; если своего он не увидел, он уйдёт.

НАЗВАНИЕ — обещает охват, а не одну тему из списка.
  Плохо: «Когда вера молчит» — это про один разбор из четырёх.
  Хорошо: «Четыре разговора о том, где Бог молчит», «Десять вопросов, которые
  задают чаще всего», «Всё, что мешает молиться».
  Число разборов в названии — хорошо, бери его из карты.

ТИЗЕР — СПИСКОМ, С ПЕРЕНОСАМИ СТРОК. Сплошным абзацем через точку с запятой
писать нельзя: человек ищет глазами свою ситуацию и в простыне её не найдёт.
 – первая строка: состояние, которое связывает все разборы. Законченное
   предложение с точкой, не зачин со списком через точку с запятой;
 – дальше КАЖДЫЙ разбор — отдельной строкой, начиная с «— ». Называй состояние
   человека, а не тему: не «про отношения с матерью», а «мать звонит — и внутри
   всё сжимается». Строка короткая, одна мысль, без точки в конце;
 – спойлеров не давай: называешь вопрос, не ответ.

Переносы строк ставь прямо в значении поля description (символ перевода строки).

Раскрыть первый разбор, а остальные упомянуть вскользь — главная ошибка:
запись из-за неё выглядит пустой, хотя в ней четыре темы. Здесь перечисление —
это не «краткое содержание», а то, ради чего тизер и нужен.

СКОЛЬКО СТРОК. Разборов до шести — назови все. Больше шести — возьми шесть
самых весомых (они идут в карте первыми) и закончи строкой о том, что
остальные вопросы разобраны там же. Карта уже отсортирована по весу.

ДЛИНА. Весь тизер — не больше 800 знаков: подпись в Telegram длиннее не
поместится и оборвётся на середине. Поэтому строки короткие, по одной мысли."""

_SYSTEM_TMPL = """Ты копирайтер духовного клуба «ЛЮБЯЩИЕ БОГА». Пишешь подписи к полным записям.

Тебе дают КАРТУ ЗАПИСИ — её уже прочитали целиком и разобрали — и выдержки из
самой расшифровки, чтобы ты слышал живой голос. Пиши по карте, расшифровка нужна
для интонации и конкретики.

Единый почерк: тёплый, вдохновляющий, художественный. Без канцелярита и без
«черновикового» тона. Один стиль от записи к записи. Обращение к слушателю на «ты».

{structure_rules}

ОБЩЕЕ ДЛЯ НАЗВАНИЯ.
Короткое, ёмкое, художественное. Просто заголовок, без лишних слов.
Примеры стиля: «Молитва о тишине в сердце», «Обретение опоры», «Свет в темноте»,
«Когда вера молчит», «Путь через сомнение».
НЕ делай служебный шаблон «Эфир. Тема», «Молитва. Тема», «Покаяние. Тема».
НЕ пересказывай содержание длинной фразой.

{kind_brief}

СТРОГО ЗАПРЕЩЕНО в названии и тизере:
- фразы-паразиты: «В этой молитве Константин нам предлагает…», «В этом эфире…»,
  «В данной записи…», «Сегодня мы…», «Костя говорит…»;
- мета-комментарии про автора, формат записи, «приглашает», «предлагает», «разбирает»;
- markdown, HTML, эмодзи, хэштеги;
- сухая аннотация и канцелярский «перечень вопросов».

Верни ТОЛЬКО JSON:
{{
  "title": "...",
  "idea_core": "глубинный смысл записи в 1 фразе",
  "description": "тизер"
}}"""


def _kind_brief(recording_kind: str) -> str:
    """Только ТОН под тип записи.

    Раньше тут же сидело и строение подписи: «название — художественное
    к главной ясности». Для разбора из нескольких ситуаций это означало
    «выбери одну» — остальные в подпись не попадали. Строение теперь задаёт
    форма записи, которую определяет первый проход.
    """
    if recording_kind == KIND_MOLITVA:
        return "ТИП ЗАПИСИ: МОЛИТВА. Тон — тихий, обращённый внутрь."
    if recording_kind == KIND_POKAYANIE:
        return "ТИП ЗАПИСИ: ПОКАЯНИЕ. Тон — атмосфера честности, без осуждения."
    if recording_kind == KIND_QA:
        return "ТИП ЗАПИСИ: ВОПРОС–ОТВЕТ. Тон — ясность, разговор по делу."
    if recording_kind == KIND_EFIR:
        return "ТИП ЗАПИСИ: ЭФИР. Тон — размышление вслух."
    return "ТИП ЗАПИСИ: не указан. Тон — тёплый разговор."


_MAP_CHUNK_CHARS = 20_000
# Потолок на число вызовов. Шесть кусков покрывают самую длинную запись в базе
# (100 тыс. знаков); при большем объёме куски просто становятся крупнее.
_MAP_MAX_CHUNKS = 6


def _split_for_map(transcript: str) -> List[str]:
    """Вся расшифровка кусками. Ничего не выбрасываем — в этом весь смысл прохода."""
    text = (transcript or "").strip()
    if not text:
        return []
    n = max(1, min(_MAP_MAX_CHUNKS, math.ceil(len(text) / _MAP_CHUNK_CHARS)))
    if n == 1:
        return [text]
    size = math.ceil(len(text) / n)
    chunks: List[str] = []
    pos = 0
    while pos < len(text):
        # Последний кусок забирает остаток: подгонка границ к переводам строк
        # укорачивает куски, и без этого в конце отваливался седьмой огрызок.
        end = len(text) if len(chunks) == n - 1 else min(len(text), pos + size)
        if end < len(text):
            nl = text.rfind("\n", pos + size // 2, end)
            if nl > pos:
                end = nl
        chunks.append(text[pos:end])
        pos = end
    return [c for c in chunks if c.strip()]


async def _complete_json(
    client,
    *,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float = 0.3,
) -> Dict[str, Any]:
    r = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    raw = r.choices[0].message.content if r.choices else ""
    m = re.search(r"\{[\s\S]*\}", raw or "")
    return json.loads(m.group(0)) if m else {}


async def build_recording_map(
    client,
    *,
    transcript: str,
    meeting_title: str,
    kind_label: str,
    model: str,
) -> Dict[str, Any]:
    """Проход 1: что вообще разобрано в записи.

    Расшифровка читается целиком, кусками. Из каждого куска выписываются
    самостоятельные темы, потом один вызов склеивает дубли и решает, сборник
    это или одна сквозная мысль.

    При сбое возвращает пустую карту — подпись тогда пишется по старой схеме,
    по выдержкам. Это хуже, но не ломает выпуск записи.
    """
    chunks = _split_for_map(transcript)
    if not chunks:
        return {}

    topics: List[Dict[str, Any]] = []
    for i, chunk in enumerate(chunks, 1):
        user = (
            f"Запись: {meeting_title} ({kind_label})\n"
            f"Кусок {i} из {len(chunks)}.\n\n{chunk}"
        )
        try:
            data = await _complete_json(
                client, model=model, system=_SYSTEM_MAP, user=user, max_tokens=1200
            )
        except Exception as e:
            logger.warning("карта записи: кусок %s/%s не разобран: %s", i, len(chunks), e)
            continue
        for t in data.get("topics") or []:
            if isinstance(t, dict) and str(t.get("title") or "").strip():
                topics.append(t)
    logger.info(
        "карта записи: кусков=%s, тем до склейки=%s (расшифровка %s знаков)",
        len(chunks),
        len(topics),
        len(transcript or ""),
    )
    if not topics:
        return {}

    listed = "\n".join(
        f"{i}. {t.get('title')} — {t.get('pain')} — {t.get('gist')}"
        for i, t in enumerate(topics, 1)
    )
    try:
        data = await _complete_json(
            client,
            model=model,
            system=_SYSTEM_MAP_REDUCE,
            user=f"Запись: {meeting_title} ({kind_label})\n\nТемы из кусков:\n{listed}",
            max_tokens=1600,
        )
    except Exception as e:
        logger.warning("карта записи: склейка не удалась: %s", e)
        return {}

    merged = [
        t for t in (data.get("topics") or [])
        if isinstance(t, dict) and str(t.get("title") or "").strip()
    ]
    if not merged:
        return {}
    # Подпись в Telegram — 1024 знака. Длинный список туда не влезет, а копирайтеру
    # проще выбирать из восьми, чем из двадцати восьми. Список уже по убыванию веса.
    total = len(merged)
    merged = merged[:8]
    shape = str(data.get("shape") or "").strip()
    if shape not in ("one_line", "collection"):
        shape = "collection" if len(merged) >= 3 else "one_line"
    rmap = {
        "shape": shape,
        "core": str(data.get("core") or "").strip(),
        "topics": merged,
        "topics_total": total,
    }
    logger.info(
        "карта записи: форма=%s, разборов=%s (в подпись пойдут %s)",
        shape,
        total,
        len(merged),
    )
    return rmap


def _format_map(rmap: Dict[str, Any]) -> str:
    topics = rmap.get("topics") or []
    lines = ["КАРТА ЗАПИСИ (запись прочитана целиком):"]
    if rmap.get("core"):
        lines.append(f"Общий смысл: {rmap['core']}")
    total = int(rmap.get("topics_total") or len(topics))
    if total > len(topics):
        lines.append(
            f"Самостоятельных разборов в записи: {total}. "
            f"Ниже {len(topics)} самых весомых, по убыванию веса."
        )
    else:
        lines.append(f"Самостоятельных разборов: {total}")
    for i, t in enumerate(topics, 1):
        pain = str(t.get("pain") or "").strip()
        line = f"{i}. {str(t.get('title') or '').strip()}"
        if pain:
            line += f" — кому это про него: {pain}"
        lines.append(line)
    return "\n".join(lines)


def _system_for_shape(shape: str, recording_kind: str) -> str:
    rules = _STRUCTURE_COLLECTION if shape == "collection" else _STRUCTURE_ONE_LINE
    return _SYSTEM_TMPL.format(
        structure_rules=rules, kind_brief=_kind_brief(recording_kind)
    )


def _format_caption(title: str, description: str) -> str:
    t = (title or "").strip()
    d = (description or "").strip()
    if t and d:
        return f"{t}\n\n{d}"
    return t or d


def _strip_parasites(text: str) -> str:
    """Убирает типичные черновиковые зачины в тизере."""
    out = (text or "").strip()
    for _ in range(3):
        cleaned = _PARASITE_RE.sub("", out, count=1).strip()
        if cleaned == out:
            break
        out = cleaned
    return out


def _normalize_title(title: str, *, recording_kind: str = "") -> str:
    """Художественное название без механического «Тип. …»."""
    t = re.sub(r"\s+", " ", (title or "").strip()).strip(" «»\"'")
    t = _MECHANICAL_PREFIX_RE.sub("", t).strip()
    if not t:
        defaults = {
            KIND_MOLITVA: "Молитва от сердца",
            KIND_POKAYANIE: "Пространство честности",
            KIND_QA: "Ясность в вопросах",
            KIND_EFIR: "Свет в пути",
        }
        return defaults.get((recording_kind or "").strip().lower(), "Запись от сердца")
    low = t.casefold()
    # После среза «Молитва.» осталось «о тишине…» → вернуть «Молитва о…»
    if (recording_kind or "").strip().lower() == KIND_MOLITVA and low.startswith(
        ("о ", "об ", "обо ", "за ", "для ")
    ):
        return f"Молитва {t}"
    return t


def _fallback_caption(
    *,
    meeting_title: str,
    summary: str,
    recording_kind: str,
) -> str:
    title = _normalize_title(
        meeting_title if (meeting_title or "").strip() not in ("", "Запись") else "",
        recording_kind=recording_kind,
    )
    base = (summary or title).strip()
    teaser = (
        f"{base[:180].rstrip('.')}. "
        "Здесь можно встретить то, что тревожит сердце, "
        "и выйти с опорой и ясностью."
    )
    return _format_caption(title, teaser)


async def build_full_voice_caption_parts(
    *,
    meeting_title: str,
    summary: str,
    transcript_excerpt: str,
    recording_kind: str,
    philosophy_hint: str = "",
    transcript: str = "",
) -> tuple[str, str, str]:
    """Возвращает (title_plain, description_plain, caption_html).

    transcript — ПОЛНАЯ расшифровка, по ней строится карта записи.
    transcript_excerpt — выдержки (начало/середина/конец), они идут копирайтеру
    для голоса и конкретики.
    """
    from config import config

    kind = (recording_kind or "").strip().lower()
    kind_label = KIND_LABELS.get(kind, "запись")
    old_title = (meeting_title or "").strip()
    excerpt = (transcript_excerpt or summary or old_title or "").strip()
    hint = (philosophy_hint or "").strip()

    title_plain = ""
    desc_plain = ""
    key = (config.OPENAI_API_KEY or "").strip()
    model = (getattr(config, "TELEMOST_AUDIO_CAPTION_MODEL", None) or "gpt-4.1").strip()
    if key and excerpt:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=key)

            rmap = await build_recording_map(
                client,
                transcript=(transcript or transcript_excerpt or ""),
                meeting_title=old_title or "Запись",
                kind_label=kind_label,
                model=model,
            )
            shape = str(rmap.get("shape") or "one_line")

            user_parts: List[str] = []
            if rmap:
                user_parts += [_format_map(rmap), ""]
            else:
                logger.warning(
                    "карта записи пустая — подпись пишем по выдержкам, как раньше"
                )
            user_parts += [
                "Составь художественное НАЗВАНИЕ и ТИЗЕР строго по правилам system.",
                "Не копируй служебные темы писем вроде «Запись встречи от …».",
                "",
            ]
            if old_title and not old_title.lower().startswith("запись встречи"):
                user_parts.append(
                    f"Старое/черновое название (можно игнорировать): {old_title}"
                )
            # summary сюда намеренно НЕ кладём. Его пишет классификатор писем —
            # он видит не эфир, а конспект Телемоста, и делает сухую аннотацию
            # для карточки в базе знаний. Как «ориентир» она тянула тизер
            # обратно в пересказ.
            user_parts += [
                "",
                "Выдержки из расшифровки — для голоса и конкретики:",
                excerpt,
            ]
            user = "\n".join(user_parts)
            if hint:
                user = f"Философия клуба:\n{hint}\n\n{user}"

            # Обрезки здесь нет намеренно. Раньше стоял user[:14000] при том,
            # что выдержки собираются с бюджетом 16 000 — и срез съедал
            # половину хвоста записи, то есть ровно тот финал, ради которого
            # выдержки и собирали из начала, середины и конца.
            data = await _complete_json(
                client,
                model=model,
                system=_system_for_shape(shape, kind),
                user=user,
                max_tokens=1200,
                temperature=0.7,
            )
            title_plain = str(data.get("title") or "").strip()
            desc_plain = str(
                data.get("description") or data.get("caption") or ""
            ).strip()
            if not title_plain and desc_plain and "\n" in desc_plain:
                first, _, rest = desc_plain.partition("\n")
                if len(first) < 120 and rest.strip():
                    title_plain = first.strip()
                    desc_plain = rest.strip()
        except Exception as e:
            logger.warning("build_full_voice_caption LLM: %s", e)

    if title_plain or desc_plain:
        title_plain = _normalize_title(title_plain or old_title, recording_kind=kind)
        desc_plain = _strip_parasites(desc_plain)
        caption_plain = _format_caption(title_plain, desc_plain)
    else:
        caption_plain = _fallback_caption(
            meeting_title=old_title or "Запись",
            summary=summary,
            recording_kind=kind,
        )
        lines = caption_plain.split("\n", 1)
        title_plain = lines[0].strip() if lines else ""
        desc_plain = lines[1].strip() if len(lines) > 1 else ""

    from telemost_audio.caption_revision import format_title_description_html

    return title_plain, desc_plain, format_title_description_html(title_plain, desc_plain)


async def build_full_voice_caption(
    *,
    meeting_title: str,
    summary: str,
    transcript_excerpt: str,
    recording_kind: str,
    philosophy_hint: str = "",
    transcript: str = "",
) -> str:
    _title, _desc, html = await build_full_voice_caption_parts(
        meeting_title=meeting_title,
        summary=summary,
        transcript_excerpt=transcript_excerpt,
        recording_kind=recording_kind,
        philosophy_hint=philosophy_hint,
        transcript=transcript,
    )
    return html
