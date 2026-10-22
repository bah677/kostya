#!/usr/bin/env python3
"""Собирает пул тем для молитв из наших же записей клуба.

Google Trends для этого канала не работает: он отдаёт футбольные таблицы и
модели телефонов, оценщик честно бракует всё подряд. А придуманный от руки
список короткий и быстро приедается.

Зато у нас есть сто пятьдесят записей эфиров и встреч, где люди своими
словами говорят, что у них болит. Это и есть настоящий источник тем — и
живее, и Костиным языком.

Скрипт читает куски расшифровок, просит модель вытащить из них житейские
беды и складывает в data/youtube_prayer/themes_<lang>.json. Повторы
отсеиваются той же мерой похожести, что и в ночном отборе.

    .venv/bin/python3 scripts/build_theme_pool.py --lang ru --target 120
    .venv/bin/python3 scripts/build_theme_pool.py --lang es --target 40
    .venv/bin/python3 scripts/build_theme_pool.py --lang ru --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# config подтягивает .env — без него нет ключа модели.
from config import config  # noqa: E402,F401

logger = logging.getLogger("themes")

_SYSTEM_RU = """
Ты редактор канала с короткими молитвами. Зрительница — женщина около
пятидесяти, русскоязычная, верующая или ищущая.

Тебе дают куски расшифровок встреч клуба, где люди рассказывают о своём.
Твоя работа — вытащить оттуда ЖИТЕЙСКИЕ БЕДЫ И СОСТОЯНИЯ, о которых можно
помолиться, и назвать каждое коротко.

ЧТО ГОДИТСЯ: тревога за взрослого сына, долги, бессонница, обида на
сестру, выгорание на работе, страх перед диагнозом, одиночество в браке,
вина за прошлое, усталость от заботы о родителях.

ЧТО НЕ ГОДИТСЯ:
- богословские понятия: благодать, покаяние, Святой Дух, заветы;
- пересказ библейских сюжетов и имена персонажей;
- организационное: расписание встреч, ссылки, домашние задания;
- имена конкретных участников и узнаваемые подробности их историй.

ФОРМАТ. Верни строго JSON без пояснений:
{"themes": [{"trend": "...", "brief": "...", "broll_query": "..."}]}

- trend — 2–5 слов строчными буквами, как сказал бы человек о себе:
  «тревога за взрослого сына», а не «проблема детско-родительских отношений».
- brief — одно предложение: о чём молитва. Начинай со слова «Молитва».
- broll_query — 3–5 слов ПО-АНГЛИЙСКИ для подбора стокового видео: место и
  свет, без людей крупным планом. Например «empty kitchen morning light».

Тем столько, сколько честно видно в тексте: 5–12. Не придумывай сверх.
""".strip()

_SYSTEM_ES = """
Eres editor de un canal de oraciones breves. La espectadora es una mujer de
unos cincuenta años, hispanohablante, creyente o en búsqueda.

Propón temas de ORACIÓN sobre dificultades cotidianas reales: deudas,
insomnio, un hijo que se alejó, cansancio en el trabajo, miedo a un
diagnóstico, soledad dentro del matrimonio, culpa por el pasado.

NO sirven: conceptos teológicos, relatos bíblicos, nombres propios,
asuntos organizativos.

FORMATO. Devuelve solo JSON:
{"themes": [{"trend": "...", "brief": "...", "broll_query": "..."}]}

- trend — 2 a 5 palabras en minúsculas, como lo diría una persona de sí misma.
- brief — una frase que empiece con «Una oración».
- broll_query — 3 a 5 palabras EN INGLÉS para buscar video de archivo.

Devuelve entre 10 y 16 temas por tanda, variados entre sí.
""".strip()


def _chunks_from_index(limit: int, seed: int) -> List[str]:
    """Куски расшифровок встреч и эфиров из боевого индекса."""
    db = _ROOT / "chroma_data" / "chroma.sqlite3"
    if not db.is_file():
        raise SystemExit(f"нет индекса: {db}")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            """
            SELECT d.id, d.string_value
            FROM embedding_metadata d
            JOIN embedding_metadata t ON t.id = d.id AND t.key = 'content_type'
            WHERE d.key = 'chroma:document'
              AND t.string_value IN ('Эфир','Эфиры','встреча клуба','встреча',
                                     'Покаяние','Вопрос-ответ')
            """
        ).fetchall()
    finally:
        con.close()
    texts = [r[1] for r in rows if r[1] and len(r[1]) > 400]
    random.Random(seed).shuffle(texts)
    return texts[:limit]


def _parse(raw: str) -> List[Dict[str, str]]:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except Exception:
            return []
    out = []
    for r in (data or {}).get("themes", []):
        if not isinstance(r, dict):
            continue
        trend = re.sub(r"\s+", " ", str(r.get("trend") or "")).strip().lower()
        brief = re.sub(r"\s+", " ", str(r.get("brief") or "")).strip()
        broll = re.sub(r"\s+", " ", str(r.get("broll_query") or "")).strip()
        if 2 <= len(trend.split()) <= 7 and len(brief) > 15:
            out.append({"trend": trend, "brief": brief,
                        "broll_query": broll or "soft warm light calm"})
    return out


_SYSTEM_CLEAN = """
Тебе дают нумерованный список тем для коротких молитв. Часть из них — одно
и то же разными словами: «не замечаю хорошего», «не умею благодарить» и
«не хватает благодарности» это одна тема.

Найди такие группы и в каждой оставь ОДНУ — ту, что звучит живее и ближе к
тому, как человек сказал бы о себе. Остальные номера из группы — в отброс.

Темы, которые просто близки по области, но описывают разные положения
(«долги и кредиты» и «потеря работы»), не трогай: это разные молитвы.

Верни строго JSON без пояснений: {"drop": [номера, которые убрать]}
""".strip()


async def _clean_duplicates(rows: List[Dict[str, str]], complete_fn) -> List[Dict[str, str]]:
    """Убирает смысловые повторы: похожесть по словам их не ловит."""
    if len(rows) < 12:
        return rows
    listed = "\n".join(f"{i}. {r['trend']}" for i, r in enumerate(rows, 1))
    raw, _ = await complete_fn(_SYSTEM_CLEAN, listed, temperature=0.2, max_tokens=2000)
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    try:
        drop = set(int(x) for x in (json.loads(text) or {}).get("drop", []))
    except Exception as e:
        logger.warning("вычистка повторов не удалась: %s", e)
        return rows
    kept = [r for i, r in enumerate(rows, 1) if i not in drop]
    print(f"смысловых повторов убрано: {len(rows) - len(kept)}")
    return kept


async def main() -> int:
    ap = argparse.ArgumentParser(description="Пул тем из записей клуба")
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--target", type=int, default=120, help="сколько тем нужно")
    # По десять кусков модель упиралась в лимит ответа и возвращала пустую
    # строку с finish=length. По четыре куска по 1200 знаков отдаёт восемь тем.
    ap.add_argument("--batch", type=int, default=4, help="кусков в одном запросе")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--clean-only", action="store_true",
                    help="только вычистить повторы в готовом файле")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from youtube_prayer.compose import deepseek_complete
    from youtube_prayer.topic_history import trends_similar
    from youtube_prayer.trends import load_theme_pool, theme_pool_path

    lang = args.lang.lower()
    system = _SYSTEM_ES if lang == "es" else _SYSTEM_RU

    # Уже собранное не теряем: скрипт можно гонять повторно и добирать.
    kept: List[Dict[str, str]] = [
        {"trend": t.trend, "brief": t.brief, "broll_query": t.broll_query}
        for t in load_theme_pool(lang)
    ]
    print(f"было в файле: {len(kept)}")

    if lang == "ru":
        texts = _chunks_from_index(limit=args.batch * 60, seed=args.seed)
        print(f"кусков расшифровок: {len(texts)}")
        batches = [texts[i:i + args.batch] for i in range(0, len(texts), args.batch)]
        users = ["\n\n---\n\n".join(t[:1200] for t in b) for b in batches]
    else:
        # Для испанского своих записей нет — просим модель держаться тем же
        # регистром житейских бед, по кругам: быт, семья, деньги, здоровье.
        rounds = ["la casa y el trabajo", "la familia y los hijos",
                  "el dinero y las deudas", "la salud y el miedo",
                  "la soledad y el matrimonio", "la culpa y el perdón",
                  "el duelo y la pérdida", "la fe y la duda"]
        users = [f"Tanda sobre: {r}. Evita repetir temas de otras tandas." for r in rounds]

    if args.clean_only:
        kept = await _clean_duplicates(kept, deepseek_complete)
        dest = theme_pool_path(lang)
        dest.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"сохранено: {len(kept)} тем → {dest}")
        return 0

    if args.dry_run:
        print(f"запросов будет: {len(users)}")
        print("\n--- первый запрос ---\n")
        print(users[0][:1200])
        return 0

    for i, user in enumerate(users, 1):
        if len(kept) >= args.target:
            break
        try:
            raw, _ = await deepseek_complete(system, user, temperature=0.7, max_tokens=3000)
        except Exception as e:
            print(f"  [{i}/{len(users)}] ошибка: {e}", file=sys.stderr)
            continue
        got = _parse(raw or "")
        added = 0
        for row in got:
            if any(trends_similar(row["trend"], k["trend"]) for k in kept):
                continue
            kept.append(row)
            added += 1
        print(f"  [{i}/{len(users)}] +{added} (всего {len(kept)})")

    kept = await _clean_duplicates(kept, deepseek_complete)
    dest = theme_pool_path(lang)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nсохранено: {len(kept)} тем → {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
