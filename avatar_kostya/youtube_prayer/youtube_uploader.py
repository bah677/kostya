"""Загрузка готовых роликов на YouTube с премьерой в заданный слот."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Sequence

from youtube_prayer.metadata import VideoMetadata
from youtube_prayer.premiere_schedule import (
    premiere_slot_datetime,
    premiere_slot_label,
    to_youtube_publish_at,
)

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]
_UPLOAD_MARKER = "youtube_upload.json"


@dataclass(frozen=True)
class YoutubeUploadResult:
    video_id: str
    url: str
    publish_at_msk: str
    premiere_label: str


def _cfg(name: str, default: Any = None) -> Any:
    try:
        from config import config as cfg

        return getattr(cfg, name, default)
    except Exception:
        return default


def youtube_upload_enabled() -> bool:
    return bool(_cfg("YT_PRAYER_YOUTUBE_UPLOAD_ENABLED", False))


def youtube_upload_langs() -> set[str]:
    raw = str(_cfg("YT_PRAYER_YOUTUBE_LANGS", "ru") or "ru")
    return {x.strip().lower() for x in raw.replace(";", ",").split(",") if x.strip()}


def _resolve_path(raw: str | Path, *, default_rel: str) -> Path:
    p = Path(raw or default_rel)
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[1] / p
    return p


def client_secrets_path() -> Path:
    return _resolve_path(
        str(_cfg("YT_PRAYER_YOUTUBE_CLIENT_SECRETS", "") or ""),
        default_rel="data/youtube_prayer/youtube_client_secret.json",
    )


def token_path() -> Path:
    return _resolve_path(
        str(_cfg("YT_PRAYER_YOUTUBE_TOKEN", "") or ""),
        default_rel="data/youtube_prayer/youtube_oauth_token.json",
    )


def premiere_hours_msk() -> List[int]:
    from youtube_prayer.premiere_schedule import parse_premiere_hours

    return parse_premiere_hours(_cfg("YT_PRAYER_YOUTUBE_PREMIERE_HOURS_MSK", "9,15,21"))


def _load_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    tok = token_path()
    if not tok.is_file():
        raise RuntimeError(
            f"YouTube OAuth: нет токена {tok}. "
            f"Запустите: cd avatar_kostya && python3 scripts/youtube_oauth_setup.py"
        )
    creds = Credentials.from_authorized_user_file(str(tok), _SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        tok.write_text(creds.to_json(), encoding="utf-8")
    if not creds.valid:
        raise RuntimeError(
            f"YouTube OAuth: токен недействителен ({tok}). "
            f"Перезапустите scripts/youtube_oauth_setup.py"
        )
    return creds


def _youtube_service():
    from googleapiclient.discovery import build

    creds = _load_credentials()
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _tags_from_metadata(meta: VideoMetadata, *, lang: str) -> List[str]:
    tags: List[str] = []
    for tag in meta.hashtags or []:
        t = str(tag).strip().lstrip("#")
        if t and t not in tags:
            tags.append(t[:30])
    if lang == "en":
        defaults = ["prayer", "Christian prayer", "faith", "comfort"]
    else:
        defaults = ["молитва", "христианская молитва", "вера", "утешение"]
    for d in defaults:
        if d not in tags:
            tags.append(d)
        if len(tags) >= 12:
            break
    return tags[:15]


def _read_upload_marker(work_dir: Path) -> Optional[dict]:
    path = work_dir / _UPLOAD_MARKER
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_upload_marker(work_dir: Path, payload: dict) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / _UPLOAD_MARKER).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _upload_sync(
    *,
    video_path: Path,
    thumbnail_path: Optional[Path],
    meta: VideoMetadata,
    lang: str,
    publish_at_msk: datetime,
    category_id: str,
    notify_subscribers: bool,
) -> YoutubeUploadResult:
    youtube = _youtube_service()
    publish_at = to_youtube_publish_at(publish_at_msk)
    default_lang = "en" if (lang or "").lower() == "en" else "ru"
    body = {
        "snippet": {
            "title": meta.title[:100],
            "description": meta.description_with_hashtags[:4900],
            "tags": _tags_from_metadata(meta, lang=lang),
            "categoryId": str(category_id or "22"),
            "defaultLanguage": default_lang,
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": publish_at,
            "selfDeclaredMadeForKids": False,
        },
    }
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        chunksize=8 * 1024 * 1024,
        resumable=True,
    )
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        notifySubscribers=bool(notify_subscribers),
        media_body=media,
    )
    response = None
    while response is None:
        _status, response = request.next_chunk()
    video_id = str(response.get("id") or "")
    if not video_id:
        raise RuntimeError("YouTube upload: пустой video_id")

    if thumbnail_path and thumbnail_path.is_file():
        try:
            thumb_media = MediaFileUpload(str(thumbnail_path), mimetype="image/jpeg")
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=thumb_media,
            ).execute()
        except Exception as e:
            logger.warning("YouTube thumbnail set failed video=%s: %s", video_id, e)

    label = premiere_slot_label(publish_at_msk)
    return YoutubeUploadResult(
        video_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        publish_at_msk=publish_at_msk.isoformat(),
        premiere_label=label,
    )


async def upload_premiere_if_enabled(
    *,
    video_path: Path,
    thumbnail_path: Optional[Path],
    metadata: VideoMetadata,
    lang: str,
    day: str,
    index: int,
    work_dir: Path,
    slots_msk: Optional[Sequence[int]] = None,
) -> Optional[YoutubeUploadResult]:
    """Идемпотентная загрузка: один раз на item_dir."""
    if not youtube_upload_enabled():
        return None
    lang_l = (lang or "ru").lower()
    if lang_l not in youtube_upload_langs():
        logger.info("YouTube upload skip lang=%s (allowed=%s)", lang_l, youtube_upload_langs())
        return None
    if not video_path.is_file():
        raise FileNotFoundError(str(video_path))

    existing = _read_upload_marker(work_dir)
    if existing and existing.get("video_id"):
        return YoutubeUploadResult(
            video_id=str(existing["video_id"]),
            url=str(existing.get("url") or ""),
            publish_at_msk=str(existing.get("publish_at_msk") or ""),
            premiere_label=str(existing.get("premiere_label") or ""),
        )

    hours = list(slots_msk) if slots_msk else premiere_hours_msk()
    publish_at = premiere_slot_datetime(
        day=day,
        index=index,
        slots_msk=hours,
    )
    category_id = str(_cfg("YT_PRAYER_YOUTUBE_CATEGORY_ID", "22") or "22")
    notify = bool(_cfg("YT_PRAYER_YOUTUBE_NOTIFY_SUBSCRIBERS", True))

    import asyncio

    result = await asyncio.to_thread(
        _upload_sync,
        video_path=video_path,
        thumbnail_path=thumbnail_path,
        meta=metadata,
        lang=lang_l,
        publish_at_msk=publish_at,
        category_id=category_id,
        notify_subscribers=notify,
    )
    _write_upload_marker(
        work_dir,
        {
            **asdict(result),
            "day": day,
            "index": index,
            "lang": lang_l,
            "uploaded_at": datetime.now().astimezone().isoformat(),
        },
    )
    logger.info(
        "YouTube premiere scheduled id=%s slot=%s file=%s",
        result.video_id,
        result.premiere_label,
        video_path.name,
    )
    return result
