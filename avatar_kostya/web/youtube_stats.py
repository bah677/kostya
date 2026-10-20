"""Статистика каналов для киностудии: читаем ночные срезы, в API не ходим.

YouTube Data API отдаёт только накопительные итоги, поэтому динамику считаем
разницей срезов — их кладёт ночной прогон в data/youtube_stats. Студия эти
файлы просто читает: страница открывается мгновенно и не тратит квоту, а на
кнопку «Обновить» снимается свежий срез поверх сегодняшнего.

Срез русского канала лежит без языкового суффикса (так сложилось исторически),
испанский — с суффиксом. Имена собирает channel_stats.snapshot_name.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from youtube_prayer.channel_stats import channel_langs, snapshots_dir
from youtube_prayer.langs import DEFAULT_LANG, normalize_lang, profile

logger = logging.getLogger(__name__)

# Сколько дней истории отдаём на график и сколько роликов в списки.
_MAX_DAYS = 30
_TOP_RECENT = 8
_RECENT_DAYS = 7


def _day_re(lang: str) -> re.Pattern:
    code = normalize_lang(lang)
    tail = r"" if code == DEFAULT_LANG else rf"_{re.escape(code)}"
    return re.compile(rf"^(\d{{4}}-\d{{2}}-\d{{2}}){tail}\.json$")


def _snapshot_days(snap_dir: Path, lang: str) -> List[Tuple[str, Path]]:
    """Дни со срезами этого канала, от старых к новым."""
    rx = _day_re(lang)
    out: List[Tuple[str, Path]] = []
    try:
        names = sorted(p.name for p in snap_dir.iterdir() if p.is_file())
    except OSError:
        return []
    for name in names:
        m = rx.match(name)
        if m:
            out.append((m.group(1), snap_dir / name))
    return out[-_MAX_DAYS:]


def _read(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("срез %s не прочитан: %s", path.name, e)
        return None


def _totals(snap: Dict[str, Any]) -> Dict[str, int]:
    ch = snap.get("channel") or {}
    return {
        "subscribers": int(ch.get("subscribers") or 0),
        "views": int(ch.get("views") or 0),
        "videos": int(ch.get("videos") or 0),
    }


def _pick_before(series: List[Dict[str, Any]], not_after: str) -> Optional[Dict[str, Any]]:
    """Последний срез не позже указанного дня.

    Срезы бывают с пропусками — ночной прогон мог не отработать, как 19-го
    октября. Привязка строго к «вчера» в такой день показывала бы пустую
    динамику, поэтому берём ближайший день, который есть, и говорим, за
    сколько суток посчитано.
    """
    found = None
    for row in series:
        if row["day"] <= not_after:
            found = row
    return found


def _delta(
    cur: Dict[str, int], prev: Optional[Dict[str, Any]], *, until: str
) -> Optional[Dict[str, Any]]:
    if not prev:
        return None
    d0 = datetime.strptime(prev["day"], "%Y-%m-%d").date()
    d1 = datetime.strptime(until, "%Y-%m-%d").date()
    out: Dict[str, Any] = {k: cur[k] - int(prev.get(k) or 0) for k in ("subscribers", "views", "videos")}
    out["days"] = max(1, (d1 - d0).days)
    out["from"] = prev["day"]
    return out


def _short_views(videos: List[Dict[str, Any]]) -> Dict[str, Any]:
    shorts = sorted(int(v.get("views") or 0) for v in videos if v.get("is_short"))
    if not shorts:
        return {"count": 0, "median": 0, "best": 0}
    return {
        "count": len(shorts),
        "median": shorts[len(shorts) // 2],
        "best": shorts[-1],
    }


def channel_stats(lang: str) -> Dict[str, Any]:
    """Всё, что нужно карточке канала: итоги, динамика, ряд, ролики."""
    code = normalize_lang(lang)
    prof = profile(code)
    out: Dict[str, Any] = {
        "lang": code,
        "label": prof.label,
        "channel_title": "",
        "channel_id": prof.youtube_channel_id,
        "taken_at": "",
        "days": 0,
        "totals": {"subscribers": 0, "views": 0, "videos": 0},
        "delta_day": None,
        "delta_week": None,
        "series": [],
        "shorts": {"count": 0, "median": 0, "best": 0},
        "recent": [],
        "note": "",
    }

    snap_dir = snapshots_dir()
    days = _snapshot_days(snap_dir, code)
    if not days:
        out["note"] = "срезов ещё нет — первый появится после ночного прогона"
        return out

    series: List[Dict[str, Any]] = []
    latest: Optional[Dict[str, Any]] = None
    for day, path in days:
        snap = _read(path)
        if not snap:
            continue
        series.append({"day": day, **_totals(snap)})
        latest = snap
        out["taken_at"] = str(snap.get("taken_at") or "")
    if latest is None:
        out["note"] = "срезы не читаются"
        return out

    cur = _totals(latest)
    last_day = series[-1]["day"]
    d = datetime.strptime(last_day, "%Y-%m-%d").date()
    out.update(
        channel_title=str((latest.get("channel") or {}).get("title") or ""),
        totals=cur,
        days=len(series),
        series=series,
        delta_day=_delta(cur, _pick_before(series[:-1], last_day), until=last_day),
        delta_week=_delta(
            cur,
            _pick_before(series[:-1], (d - timedelta(days=_RECENT_DAYS)).isoformat()),
            until=last_day,
        ),
    )

    videos = list(latest.get("videos") or [])
    out["shorts"] = _short_views(videos)

    # Ролики последней недели: сколько набрали всего и сколько за сутки.
    prev_videos = {}
    prev_row = _pick_before(series[:-1], last_day)
    if prev_row:
        prev_snap = _read(dict(days)[prev_row["day"]]) or {}
        prev_videos = {v.get("id"): v for v in (prev_snap.get("videos") or [])}
    since = (d - timedelta(days=_RECENT_DAYS)).isoformat()
    recent = []
    for v in videos:
        pub = str(v.get("published") or "")
        if pub[:10] < since:
            continue
        was = prev_videos.get(v.get("id"))
        recent.append(
            {
                "id": v.get("id", ""),
                "title": str(v.get("title") or ""),
                "published": pub,
                "sec": int(v.get("sec") or 0),
                "views": int(v.get("views") or 0),
                "likes": int(v.get("likes") or 0),
                "comments": int(v.get("comments") or 0),
                "views_day": (
                    int(v.get("views") or 0) - int(was.get("views") or 0) if was else None
                ),
            }
        )
    recent.sort(key=lambda r: -r["views"])
    out["recent"] = recent[:_TOP_RECENT]
    if len(series) < 2:
        out["note"] = "это первый срез — динамика появится со второго"
    return out


def all_channel_stats() -> List[Dict[str, Any]]:
    return [channel_stats(code) for code in channel_langs()]


def refresh_now() -> Dict[str, str]:
    """Снять свежий срез по всем каналам поверх сегодняшнего."""
    from youtube_prayer.channel_stats import snapshot_all_channels

    return snapshot_all_channels()
