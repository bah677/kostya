"""Ожидание письма с записью и скачивание аудио-записи эфира."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

from config import config
from telemost_mail.imap_client import YandexImapClient
from telemost_mail.recording_parse import _url_video_score
from telemost_mail.recording_resolver import scan_imap_for_recording
from telemost_mail.video_download import download_recording_video
from telemost_mail.webdav_recording import (
    audio_duration_is_full,
    download_telemost_audio_webdav,
)

logger = logging.getLogger(__name__)

NotifyFn = Callable[[str], Any]


async def _notify(notify: Optional[NotifyFn], text: str) -> None:
    if notify is None:
        return
    try:
        result = notify(text)
        if asyncio.iscoroutine(result):
            await result
    except Exception as e:
        logger.warning("audio_resolver notify: %s", e)


def _has_recording_links(row: Optional[dict]) -> bool:
    """Письмо со ссылками на видео и аудио (как договаривались)."""
    if not row:
        return False
    video = ((row.get("video_url") or "").strip())
    audio = ((row.get("audio_url") or "").strip())
    return _url_video_score(video) > 0 and _url_video_score(audio) > 0


async def wait_and_download_audio(
    meeting_id: str,
    *,
    storage,
    imap: YandexImapClient,
    notify: Optional[NotifyFn] = None,
) -> Optional[str]:
    mid = (meeting_id or "").strip()
    if not mid:
        return None

    wait_sec = int(getattr(config, "TELEMOST_SHORTS_WAIT_RECORDING_SEC", 7200) or 7200)
    poll_sec = int(getattr(config, "TELEMOST_SHORTS_POLL_INTERVAL_SEC", 120) or 120)
    dest = getattr(config, "TELEMOST_AUDIO_DIR", "data/telemost_audio")
    cached = Path(dest) / f"{mid}_audio.mp3"

    login = (getattr(config, "YANDEX_DISK_LOGIN", "") or "").strip()
    password = (getattr(config, "YANDEX_DISK_PASSWORD", "") or "").strip()
    has_webdav = bool(login and password)

    # После N неудач по публичной ссылке не долбим её каждый цикл (404 / битый yt-dlp).
    public_fail_streak = 0
    skip_public_url = False
    notified_wait = False
    notified_fail = False
    deadline = time.monotonic() + max(60, wait_sec)

    async def _try_once(url: str) -> Optional[str]:
        nonlocal public_fail_streak, skip_public_url
        if has_webdav:
            path = await download_telemost_audio_webdav(mid, dest_dir=dest)
            if path and audio_duration_is_full(path):
                return path
            if path:
                logger.warning(
                    "audio_resolver: webdav path still short meeting_id=%s path=%s",
                    mid,
                    path,
                )
        if url and not skip_public_url:
            path = await asyncio.to_thread(
                download_recording_video,
                url,
                f"{mid}_audio",
                dest_dir=dest,
            )
            if path and audio_duration_is_full(path):
                public_fail_streak = 0
                return path
            if path:
                public_fail_streak = 0
                return path
            public_fail_streak += 1
            if public_fail_streak >= 2:
                skip_public_url = True
                logger.warning(
                    "audio_resolver: skip public url after fails meeting_id=%s url=%s",
                    mid,
                    url[:80],
                )
        return None

    while time.monotonic() < deadline:
        row = await storage.get_telemost_recording(mid)
        local = ((row or {}).get("local_audio_path") or "").strip()
        if local and Path(local).is_file() and Path(local).stat().st_size > 10_000:
            if audio_duration_is_full(local):
                return local

        if (
            cached.is_file()
            and cached.stat().st_size > 10_000
            and audio_duration_is_full(cached)
        ):
            await storage.set_telemost_recording_local_audio_path(mid, str(cached))
            return str(cached)

        # Без письма со ссылками на видео+аудио — только ждём (WebDAV не трогаем).
        if not _has_recording_links(row):
            await scan_imap_for_recording(imap, mid, storage=storage, limit=80)
            row = await storage.get_telemost_recording(mid)

        if not _has_recording_links(row):
            if not notified_wait:
                await _notify(
                    notify,
                    f"⏳ Жду письмо со ссылками на <b>видео и аудио</b> "
                    f"№<code>{mid}</code> (до {wait_sec // 60} мин.)…",
                )
                notified_wait = True
            await asyncio.sleep(max(30, poll_sec))
            continue

        url = ((row or {}).get("audio_url") or "").strip()
        await _notify(notify, "⬇️ Скачиваю аудио-запись…")
        path = await _try_once(url)
        if path:
            await storage.set_telemost_recording_local_audio_path(mid, path)
            return path
        if not notified_fail:
            await _notify(
                notify,
                f"⚠️ Аудио №<code>{mid}</code> пока не скачалось "
                f"(повтор каждые {max(30, poll_sec)} с)…",
            )
            notified_fail = True

        await asyncio.sleep(max(30, poll_sec))

    logger.warning("audio_resolver: timeout meeting_id=%s", mid)
    return None
