"""Загрузка готовых роликов на YouTube с премьерой в заданный слот."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Sequence

from youtube_prayer.langs import (
    DEFAULT_LANG,
    normalize_lang,
    premiere_hours_for,
    profile,
)
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
_OAUTH_ALERT_MARKER = "oauth_alert_sent.json"
_OAUTH_ALERT_COOLDOWN_SEC = 6 * 3600

_OAUTH_REISSUE_HINT = (
    "cd /home/appuser/dev/kostya/avatar_kostya && "
    ".venv/bin/python scripts/youtube_oauth_setup.py"
)


class YoutubeOAuthError(RuntimeError):
    """OAuth-токен YouTube протух / отозван — нужен перевыпуск."""


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


def client_secrets_path(lang: str = "") -> Path:
    """Секреты OAuth. У каждого языка свой канал, значит и свой проект."""
    code = normalize_lang(lang) if lang else ""
    if code and code != DEFAULT_LANG:
        raw = str(_cfg(f"YT_PRAYER_YOUTUBE_CLIENT_SECRETS_{code.upper()}", "") or "")
        if raw:
            return _resolve_path(raw, default_rel=raw)
    return _resolve_path(
        str(_cfg("YT_PRAYER_YOUTUBE_CLIENT_SECRETS", "") or ""),
        default_rel="data/youtube_prayer/youtube_client_secret.json",
    )


def token_path(lang: str = "") -> Path:
    """Токен канала этого языка.

    Каналы разные — испанские ролики должны уходить на испанский канал, а не
    на «Любящие Бога». Если для языка токен не задан, берётся общий: русский
    канал продолжает работать ровно как раньше.
    """
    code = normalize_lang(lang) if lang else ""
    if code and code != DEFAULT_LANG:
        raw = str(_cfg(f"YT_PRAYER_YOUTUBE_TOKEN_{code.upper()}", "") or "")
        if raw:
            return _resolve_path(raw, default_rel=raw)
        default_rel = f"data/youtube_prayer/youtube_oauth_token_{code}.json"
        guess = _resolve_path("", default_rel=default_rel)
        if guess.is_file():
            return guess
    return _resolve_path(
        str(_cfg("YT_PRAYER_YOUTUBE_TOKEN", "") or ""),
        default_rel="data/youtube_prayer/youtube_oauth_token.json",
    )


def premiere_hours_msk() -> List[int]:
    from youtube_prayer.premiere_schedule import parse_premiere_hours

    return parse_premiere_hours(_cfg("YT_PRAYER_YOUTUBE_PREMIERE_HOURS_MSK", "9,15,21"))


def is_youtube_oauth_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    needles = (
        "invalid_grant",
        "token has been expired or revoked",
        "refresherror",
        "youtubeoautherror",
        "токен недействителен",
        "нет токена",
    )
    return any(n in text for n in needles)


def format_oauth_reissue_html(detail: str = "") -> str:
    detail_line = f"\n<code>{_esc_html(detail[:500])}</code>\n" if detail else "\n"
    return (
        "🚨 <b>YouTube OAuth: токен протух / отозван</b>\n"
        "Автозагрузка на канал остановлена — нужен перевыпуск токена."
        f"{detail_line}\n"
        "<b>На сервере:</b>\n"
        f"<code>{_esc_html(_OAUTH_REISSUE_HINT)}</code>\n\n"
        "После OK: <code>/yt_prayer force</code>"
    )


def _esc_html(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _oauth_alert_marker_path() -> Path:
    return token_path().parent / _OAUTH_ALERT_MARKER


def _oauth_alert_allowed() -> bool:
    path = _oauth_alert_marker_path()
    if not path.is_file():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        sent_at = float(data.get("sent_at") or 0)
        return (time.time() - sent_at) >= _OAUTH_ALERT_COOLDOWN_SEC
    except Exception:
        return True


def _mark_oauth_alert_sent(detail: str) -> None:
    path = _oauth_alert_marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "sent_at": time.time(),
                "sent_at_iso": datetime.now().astimezone().isoformat(),
                "detail": (detail or "")[:500],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


async def notify_youtube_oauth_problem(
    bot: Any,
    *,
    chat_id: int,
    topic_id: int = 0,
    detail: str = "",
    force: bool = False,
) -> bool:
    """Алерт в админский топик YouTube. Не чаще раза в 6ч (если не force)."""
    if not chat_id:
        return False
    if not force and not _oauth_alert_allowed():
        logger.info("YouTube OAuth alert suppressed (cooldown)")
        return False
    text = format_oauth_reissue_html(detail)
    kwargs = {"message_thread_id": int(topic_id)} if topic_id else {}
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML", **kwargs)
        _mark_oauth_alert_sent(detail)
        return True
    except Exception as e:
        logger.error("YouTube OAuth admin alert failed: %s", e)
        return False


def _load_credentials(lang: str = ""):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    tok = token_path(lang)
    if not tok.is_file():
        raise YoutubeOAuthError(
            f"нет токена {tok}. Запустите: {_OAUTH_REISSUE_HINT}"
        )
    if not client_secrets_path(lang).is_file():
        # токен может жить без файла secret, но перевыпуск без него невозможен
        logger.warning(
            "YouTube client secret отсутствует: %s", client_secrets_path(lang)
        )
    creds = Credentials.from_authorized_user_file(str(tok), _SCOPES)
    try:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            tok.write_text(creds.to_json(), encoding="utf-8")
    except Exception as e:
        if is_youtube_oauth_error(e):
            raise YoutubeOAuthError(
                f"refresh failed: {e}. Перевыпуск: {_OAUTH_REISSUE_HINT}"
            ) from e
        raise
    if not creds.valid:
        raise YoutubeOAuthError(
            f"токен недействителен ({tok}). Перевыпуск: {_OAUTH_REISSUE_HINT}"
        )
    return creds


def probe_youtube_oauth(
    *, require_upload_enabled: bool = True, lang: str = ""
) -> None:
    """Проверка токена до тяжёлого рендера. Бросает YoutubeOAuthError."""
    if require_upload_enabled and not youtube_upload_enabled():
        return
    _load_credentials(lang)


def _youtube_service(lang: str = ""):
    from googleapiclient.discovery import build

    creds = _load_credentials(lang)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _tags_from_metadata(meta: VideoMetadata, *, lang: str) -> List[str]:
    tags: List[str] = []
    for tag in meta.hashtags or []:
        t = str(tag).strip().lstrip("#")
        if t and t not in tags:
            tags.append(t[:30])
    for d in profile(lang).default_tags:
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
    youtube = _youtube_service(lang)
    publish_at = to_youtube_publish_at(publish_at_msk)
    default_lang = profile(lang).youtube_lang
    body = {
        "snippet": {
            "title": meta.title[:100],
            "description": meta.description_with_hashtags[:4900],
            "tags": _tags_from_metadata(meta, lang=lang),
            "categoryId": str(category_id or "22"),
            "defaultLanguage": default_lang,
            # Без него YouTube не знает язык дорожки: хуже таргетинг на
            # русскоязычных и не включается автоперевод метаданных.
            "defaultAudioLanguage": default_lang,
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

    label = premiere_slot_label(publish_at_msk, tz=profile(lang).tz)
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

    tz = profile(lang_l).tz
    hours = (
        list(slots_msk)
        if slots_msk
        else premiere_hours_for(
            lang_l, env_prefix="YT_PRAYER_YOUTUBE_PREMIERE_HOURS_MSK"
        )
    )
    # Часы понимаются в поясе аудитории канала: «утро по дороге» должно быть
    # утром у зрителя, а не у нас.
    publish_at = premiere_slot_datetime(
        day=day,
        index=index,
        slots_msk=hours,
        tz=tz,
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
