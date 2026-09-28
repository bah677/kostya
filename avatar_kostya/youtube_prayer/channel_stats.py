"""Ночной срез статистики канала и короткий разбор динамики через DeepSeek.

YouTube Data API отдаёт только накопительные итоги — «сколько всего просмотров
у ролика». Дневного прироста там нет, а Analytics API требует отдельного scope
(yt-analytics.readonly), которого у токена нет. Поэтому каждую ночь кладём срез
в data/youtube_stats/YYYY-MM-DD.json и считаем динамику как разницу срезов.
Первый запуск динамику показать не может — это нормально, со второго уже да.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_MSK = ZoneInfo("Europe/Moscow")
_SNAP_DIR = "youtube_stats"
_SHORT_MAX_SEC = 180


# ── сбор ────────────────────────────────────────────────────────────────────


def _duration_sec(iso: str) -> int:
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mi, s = (int(g or 0) for g in m.groups())
    return h * 3600 + mi * 60 + s


def collect_snapshot() -> Dict[str, Any]:
    """Итоги канала + статистика по каждому ролику на текущий момент."""
    from youtube_prayer.youtube_uploader import _youtube_service

    yt = _youtube_service()
    ch = yt.channels().list(part="snippet,statistics,contentDetails", mine=True).execute()
    c = ch["items"][0]
    st = c["statistics"]

    uploads = c["contentDetails"]["relatedPlaylists"]["uploads"]
    ids: List[str] = []
    token = None
    while True:
        r = yt.playlistItems().list(
            part="contentDetails",
            playlistId=uploads,
            maxResults=50,
            pageToken=token,
        ).execute()
        ids += [i["contentDetails"]["videoId"] for i in r["items"]]
        token = r.get("nextPageToken")
        if not token or len(ids) >= 200:
            break

    videos: List[Dict[str, Any]] = []
    for i in range(0, len(ids), 50):
        r = yt.videos().list(
            part="snippet,statistics,contentDetails", id=",".join(ids[i : i + 50])
        ).execute()
        for v in r["items"]:
            vst, sn = v.get("statistics", {}), v["snippet"]
            sec = _duration_sec(v["contentDetails"].get("duration", ""))
            videos.append(
                {
                    "id": v["id"],
                    "title": sn.get("title", ""),
                    "published": sn.get("publishedAt", "")[:16].replace("T", " "),
                    "sec": sec,
                    "is_short": sec <= _SHORT_MAX_SEC,
                    "views": int(vst.get("viewCount", 0) or 0),
                    "likes": int(vst.get("likeCount", 0) or 0),
                    "comments": int(vst.get("commentCount", 0) or 0),
                }
            )

    return {
        "taken_at": datetime.now(_MSK).isoformat(timespec="seconds"),
        "channel": {
            "title": c["snippet"].get("title", ""),
            "subscribers": int(st.get("subscriberCount", 0) or 0),
            "videos": int(st.get("videoCount", 0) or 0),
            "views": int(st.get("viewCount", 0) or 0),
        },
        "videos": videos,
    }


def snapshots_dir() -> Path:
    """data/youtube_stats рядом с остальными данными бота."""
    from config import config

    raw = getattr(config, "YT_STATS_DIR", None) or "data/youtube_stats"
    p = Path(raw)
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[1] / p
    return p


def save_snapshot(snap_dir: Path, snap: Dict[str, Any], *, day: str) -> Path:
    snap_dir.mkdir(parents=True, exist_ok=True)
    path = snap_dir / f"{day}.json"
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_snapshot(snap_dir: Path, day: str) -> Optional[Dict[str, Any]]:
    path = snap_dir / f"{day}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("снимок %s не прочитан: %s", path.name, e)
        return None


# ── сравнение ───────────────────────────────────────────────────────────────


def _delta(cur: Dict[str, Any], prev: Optional[Dict[str, Any]]) -> Optional[Dict[str, int]]:
    if not prev:
        return None
    a, b = cur["channel"], prev["channel"]
    return {
        "subscribers": a["subscribers"] - b["subscribers"],
        "views": a["views"] - b["views"],
        "videos": a["videos"] - b["videos"],
    }


def build_payload(
    snap_dir: Path, snap: Dict[str, Any], *, day: str, days_back: int = 7
) -> Dict[str, Any]:
    """Итоги, приросты за сутки и за неделю, ролики последних дней."""
    d = datetime.strptime(day, "%Y-%m-%d").date()
    prev_day = load_snapshot(snap_dir, (d - timedelta(days=1)).isoformat())
    week_ago = load_snapshot(snap_dir, (d - timedelta(days=days_back)).isoformat())

    prev_views = {v["id"]: v for v in (prev_day or {}).get("videos", [])}
    since = (d - timedelta(days=days_back)).isoformat()
    recent = []
    for v in snap["videos"]:
        if v["published"][:10] < since:
            continue
        was = prev_views.get(v["id"])
        recent.append(
            {
                **{k: v[k] for k in ("title", "published", "sec", "views", "likes", "comments")},
                "views_за_сутки": (v["views"] - was["views"]) if was else None,
            }
        )
    recent.sort(key=lambda r: -r["views"])

    shorts = [v for v in snap["videos"] if v["is_short"]]
    return {
        "дата_отчёта": day,
        "итоги_канала": snap["channel"],
        "прирост_за_сутки": _delta(snap, prev_day),
        f"прирост_за_{days_back}_дней": _delta(snap, week_ago),
        "всего_шортсов": len(shorts),
        "медиана_просмотров_шортса": (
            sorted(v["views"] for v in shorts)[len(shorts) // 2] if shorts else 0
        ),
        "ролики_за_период": recent[:20],
        "переходы_в_бота": "нет данных: в описаниях роликов нет ссылки на бота",
    }


# ── разбор ──────────────────────────────────────────────────────────────────


ANALYSIS_SYSTEM_PROMPT = """
Ты аналитик YouTube-канала. Ниша: христианские молитвы, короткие вертикальные
ролики (Shorts) на русском языке. Канал маленький, растёт с нуля.

Тебе дают выгрузку статистики: итоги канала на сегодня, прирост за сутки и за
неделю, и список роликов за последние дни с их цифрами. Поле «views_за_сутки»
равно null, если вчерашнего среза не было.

ПРИОРИТЕТЫ РАЗБОРА, строго в этом порядке:
1. Подписчики — сколько прибавилось и что этому предшествовало.
2. Просмотры — растут или падают, на каких темах и в какое время публикации.
3. Переходы в бота — если в данных стоит «нет данных», напиши об этом одной
   строкой и не выдумывай оценок.

ФОРМАТ ОТВЕТА. Это читают с телефона за полминуты. Максимум 200 слов.
Ровно четыре блока, каждый с этого заголовка и без markdown-разметки:

ДИНАМИКА
Две-три строки. Подписчики и просмотры в абсолютных числах со знаком
изменения. Обязательно «стало из было».

ЧТО СРАБОТАЛО
Один-два конкретных ролика: название и цифра. Чем именно они отличались от
остальных — тема, формулировка заголовка, час публикации. Только то, что
видно в данных.

ЧТО ПРОСЕЛО
Один-два конкретных ролика или закономерность с цифрами.

ЗАВТРА
Ровно одно действие, самое важное. Конкретное и проверяемое: какую тему взять,
что поменять в заголовке, в какой час публиковать. Не список.

ЖЁСТКИЕ ПРАВИЛА:
- Только выводы, которые подтверждаются цифрами. Причин, которых в данных нет,
  не придумывай.
- Каждое число с контекстом: «+12 подписчиков (89 → 101)», а не «рост
  подписчиков».
- Выборка маленькая — так и скажи. Один ролик не тренд, два дня не динамика.
- Никаких общих советов: «делайте качественный контент», «работайте над
  вовлечённостью», «оптимизируйте SEO» — это мусор, за него штраф.
- Если вчерашнего среза нет, честно напиши, что это первый замер, и разбери
  только текущий срез без выводов о динамике.
- Пиши по-русски, простыми короткими предложениями.
""".strip()


async def analyze(payload: Dict[str, Any]) -> Optional[str]:
    from youtube_prayer.compose import deepseek_complete

    user = json.dumps(payload, ensure_ascii=False, indent=2)
    text, _finish = await deepseek_complete(
        ANALYSIS_SYSTEM_PROMPT,
        f"Выгрузка статистики:\n\n{user}",
        temperature=0.3,
        max_tokens=1200,
    )
    return (text or "").strip() or None


# ── оркестратор ─────────────────────────────────────────────────────────────


async def run_daily_channel_report(
    bot: Any,
    *,
    chat_id: int,
    topic_id: int = 0,
    day: Optional[str] = None,
) -> Optional[str]:
    """Снять срез, сравнить с прошлыми, разобрать через DeepSeek и отправить.

    Возвращает текст разбора или None. Ошибки не роняют ночной прогон —
    отчёт вторичен по отношению к самой генерации роликов.
    """
    import asyncio

    day = day or datetime.now(_MSK).strftime("%Y-%m-%d")
    snap_dir = snapshots_dir()
    try:
        snap = await asyncio.to_thread(collect_snapshot)
    except Exception as e:
        logger.warning("срез статистики не снялся: %s", e)
        return None

    save_snapshot(snap_dir, snap, day=day)
    payload = build_payload(snap_dir, snap, day=day)

    try:
        analysis = await analyze(payload)
    except Exception as e:
        logger.warning("разбор DeepSeek не удался: %s", e)
        analysis = None

    ch = payload["итоги_канала"]
    d = payload.get("прирост_за_сутки")
    head = (
        f"📊 <b>Канал за {day}</b>\n"
        f"Подписчиков: {ch['subscribers']}"
        + (f" ({d['subscribers']:+d} за сутки)" if d else " (первый замер)")
        + f"\nПросмотров всего: {ch['views']}"
        + (f" ({d['views']:+d} за сутки)" if d else "")
    )
    text = head + (f"\n\n{analysis}" if analysis else "\n\n(разбор не собрался)")

    kwargs = {"message_thread_id": int(topic_id)} if topic_id else {}
    try:
        await bot.send_message(chat_id, text[:4000], parse_mode="HTML", **kwargs)
    except Exception as e:
        logger.warning("отчёт не отправлен: %s", e)
    return analysis
