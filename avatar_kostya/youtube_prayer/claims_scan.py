"""Сверка: какие выложенные ролики заблокированы и из-за какого отрезка музыки.

Идёт перед ночной генерацией. Нашла заблокированный ролик — подняла из его
рабочей папки audio_mix.json, узнала трек и секунды, внесла отрезок в
чёрный список. Сегодняшние ролики на этот кусок уже не попадут.

Блокировку видно по contentDetails.regionRestriction.blocked: при заявке с
политикой «блокировать» там оказываются все 249 стран. Детали самой заявки
Data API не отдаёт — название композиции видно только в Studio, — но для
нашей задачи достаточно знать окно музыки, а его мы пишем сами.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from youtube_prayer.bg_claims import add_claim

logger = logging.getLogger(__name__)

# Все страны мира: столько в blocked оказывается при политике «блокировать».
_FULL_BLOCK_MIN = 200


def _data_root() -> Path:
    return Path(__file__).resolve().parents[1] / "data"


def _index_uploads() -> Dict[str, Path]:
    """video_id → папка ролика. Обходим все пайплайны разом."""
    out: Dict[str, Path] = {}
    root = _data_root()
    if not root.is_dir():
        return out
    for marker in root.glob("youtube_*/*/*/youtube_upload.json"):
        try:
            vid = str(json.loads(marker.read_text(encoding="utf-8")).get("video_id") or "")
        except Exception:
            continue
        if vid:
            out[vid] = marker.parent
    return out


def _blocked_video_ids(lang: str) -> List[Tuple[str, str]]:
    """(video_id, заголовок) роликов, закрытых во всех странах."""
    from youtube_prayer.youtube_uploader import _youtube_service

    yt = _youtube_service(lang)
    ch = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0]
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    ids: List[str] = []
    token: Optional[str] = None
    while True:
        r = yt.playlistItems().list(
            part="contentDetails", playlistId=uploads, maxResults=50, pageToken=token
        ).execute()
        ids += [i["contentDetails"]["videoId"] for i in r["items"]]
        token = r.get("nextPageToken")
        if not token:
            break
    blocked: List[Tuple[str, str]] = []
    for i in range(0, len(ids), 50):
        vs = yt.videos().list(
            part="snippet,contentDetails", id=",".join(ids[i : i + 50])
        ).execute()["items"]
        for v in vs:
            rr = (v.get("contentDetails") or {}).get("regionRestriction") or {}
            if len(rr.get("blocked") or []) >= _FULL_BLOCK_MIN:
                blocked.append((v["id"], v["snippet"]["title"]))
    return blocked


def scan_and_update(langs: Tuple[str, ...] = ("ru", "es")) -> Dict[str, object]:
    """Возвращает сводку для админского отчёта."""
    index = _index_uploads()
    total_blocked = 0
    added: List[str] = []
    untraced: List[str] = []
    errors: List[str] = []

    for lang in langs:
        try:
            blocked = _blocked_video_ids(lang)
        except Exception as e:
            logger.warning("claims_scan: канал %s не прочитан: %s", lang, e)
            errors.append(f"{lang}: {type(e).__name__}")
            continue
        total_blocked += len(blocked)
        for vid, title in blocked:
            item = index.get(vid)
            mix = (item / "audio_mix.json") if item else None
            if not mix or not mix.is_file():
                untraced.append(f"{vid} · {title[:40]}")
                continue
            try:
                d = json.loads(mix.read_text(encoding="utf-8"))
                track = d.get("bg_track")
                start = d.get("bg_start_sec")
                dur = d.get("duration_sec")
            except Exception:
                untraced.append(f"{vid} · {title[:40]}")
                continue
            if not track or start is None or dur is None:
                untraced.append(f"{vid} · {title[:40]}")
                continue
            if add_claim(track, float(start), float(dur)):
                added.append(f"{track} {float(start):.0f}–{float(start) + float(dur):.0f} с")

    return {
        "заблокировано_всего": total_blocked,
        "новых_отрезков": len(added),
        "отрезки": added,
        "без_следов": untraced,
        "ошибки": errors,
    }


def format_report(summary: Dict[str, object]) -> str:
    n_blocked = int(summary.get("заблокировано_всего") or 0)
    added = list(summary.get("отрезки") or [])
    untraced = list(summary.get("без_следов") or [])
    errors = list(summary.get("ошибки") or [])
    if not n_blocked and not errors:
        return ""
    lines = [f"🎵 Заблокировано роликов: {n_blocked}"]
    if added:
        lines.append("В чёрный список музыки добавлено:")
        lines += [f"  · {a}" for a in added]
    if untraced:
        lines.append(
            f"Без следов микса ({len(untraced)}) — отрезок не вычислить, "
            "это ролики до появления audio_mix.json:"
        )
        lines += [f"  · {u}" for u in untraced[:5]]
    if errors:
        lines.append("Ошибки: " + ", ".join(errors))
    return "\n".join(lines)
