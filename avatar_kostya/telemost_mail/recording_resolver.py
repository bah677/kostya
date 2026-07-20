"""Ожидание письма с записью и скачивание видео по meeting_id."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Optional

from config import config
from telemost_mail.imap_client import FetchedMail, YandexImapClient
from telemost_mail.recording_parse import parse_recording_email, pick_best_video_url, _url_video_score
from telemost_mail.video_download import download_recording_video
from telemost_mail.webdav_recording import download_telemost_recording_webdav

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
        logger.warning("recording_resolver notify: %s", e)


async def scan_imap_for_recording(
    imap: YandexImapClient,
    meeting_id: str,
    *,
    storage,
    limit: int = 40,
) -> Optional[str]:
    """Ищет письмо с записью в IMAP, сохраняет в БД, возвращает local_path если уже скачан."""

    def _scan() -> Optional[tuple[str, str, str, str]]:
        import email as email_lib
        import imaplib

        mid = (meeting_id or "").strip()
        if not mid:
            return None
        conn: Optional[imaplib.IMAP4_SSL] = None
        try:
            conn = imaplib.IMAP4_SSL(imap._host, imap._port)
            conn.login(imap._login, imap._password)
            conn.select(imap._folder)
            typ, data = conn.uid("search", None, "ALL")
            if typ != "OK" or not data or not data[0]:
                return None
            uids = [u.decode() for u in data[0].split()][-limit:]
            for uid in reversed(uids):
                typ, msg_data = conn.uid("fetch", uid, "(RFC822)")
                if typ != "OK" or not msg_data:
                    continue
                raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
                if not raw:
                    continue
                msg = email_lib.message_from_bytes(raw)
                from telemost_mail.imap_client import _decode_mime_header, _extract_body_and_txt

                subj = _decode_mime_header(msg.get("Subject"))
                body, _txt = _extract_body_and_txt(msg)
                rec = parse_recording_email(subj, body)
                if not rec or (not rec.video_url and not rec.audio_url):
                    continue
                if rec.meeting_id and rec.meeting_id != mid:
                    continue
                if not rec.meeting_id:
                    if mid not in subj and mid not in body:
                        continue
                return (
                    uid,
                    rec.video_url or "",
                    rec.audio_url or "",
                    subj,
                    msg.get("Message-ID") or "",
                )
            return None
        except Exception as e:
            logger.error("scan_imap_for_recording: %s", e)
            return None
        finally:
            if conn is not None:
                try:
                    conn.logout()
                except Exception:
                    pass

    hit = await asyncio.to_thread(_scan)
    if not hit:
        return None
    uid, video_url, audio_url, subject, message_id = hit
    existing = await storage.get_telemost_recording(meeting_id)
    if existing:
        old_url = (existing.get("video_url") or "").strip()
        if _url_video_score(old_url) > _url_video_score(video_url):
            video_url = old_url
        old_audio = (existing.get("audio_url") or "").strip()
        if _url_video_score(old_audio) > _url_video_score(audio_url):
            audio_url = old_audio
    await storage.upsert_telemost_recording(
        meeting_id=meeting_id,
        imap_uid=uid,
        message_id=message_id,
        subject=subject,
        video_url=video_url,
        audio_url=audio_url,
    )
    row = await storage.get_telemost_recording(meeting_id)
    if row and (row.get("local_path") or "").strip():
        return row["local_path"]
    return None


def _has_video_and_audio_links(row: Optional[dict]) -> bool:
    if not row:
        return False
    video = (row.get("video_url") or "").strip()
    audio = (row.get("audio_url") or "").strip()
    return _url_video_score(video) > 0 and _url_video_score(audio) > 0


async def wait_and_download_recording(
    meeting_id: str,
    *,
    storage,
    imap: YandexImapClient,
    notify: Optional[NotifyFn] = None,
) -> Optional[str]:
    """
    Ждёт письмо со ссылками на видео и аудио, затем скачивает видео.
    WebDAV / публичная ссылка — только после письма.
    """
    mid = (meeting_id or "").strip()
    if not mid:
        return None

    wait_sec = int(getattr(config, "TELEMOST_SHORTS_WAIT_RECORDING_SEC", 7200) or 7200)
    poll_sec = int(getattr(config, "TELEMOST_SHORTS_POLL_INTERVAL_SEC", 120) or 120)
    dest = getattr(config, "TELEMOST_SHORTS_VIDEO_DIR", "data/telemost_video")

    async def _try_download(url: str) -> Optional[str]:
        login = (getattr(config, "YANDEX_DISK_LOGIN", "") or "").strip()
        password = (getattr(config, "YANDEX_DISK_PASSWORD", "") or "").strip()
        if login and password:
            await _notify(notify, "⬇️ Скачиваю запись с Я.Диска…")
            path = await download_telemost_recording_webdav(mid, dest_dir=dest)
            if path:
                return path
        if url:
            path = await asyncio.to_thread(
                download_recording_video,
                url,
                mid,
                dest_dir=dest,
            )
            if path:
                return path
        if login and password:
            return await download_telemost_recording_webdav(mid, dest_dir=dest)
        return None

    deadline = time.monotonic() + max(60, wait_sec)
    notified_wait = False
    notified_download_fail = False

    while time.monotonic() < deadline:
        row = await storage.get_telemost_recording(mid)
        if row:
            local = (row.get("local_path") or "").strip()
            if local:
                from pathlib import Path

                if Path(local).is_file() and Path(local).stat().st_size > 10_000:
                    return local

        if not _has_video_and_audio_links(row):
            await scan_imap_for_recording(imap, mid, storage=storage, limit=80)
            row = await storage.get_telemost_recording(mid)

        if not _has_video_and_audio_links(row):
            if not notified_wait:
                await _notify(
                    notify,
                    f"⏳ Жду письмо со ссылками на <b>видео и аудио</b> "
                    f"№<code>{mid}</code> (до {wait_sec // 60} мин.)…",
                )
                notified_wait = True
            await asyncio.sleep(max(30, poll_sec))
            continue

        url = ((row or {}).get("video_url") or "").strip()
        await _notify(notify, "⬇️ Скачиваю запись эфира…")
        path = await _try_download(url)
        if path:
            await storage.set_telemost_recording_local_path(mid, path)
            return path
        if not notified_download_fail:
            await _notify(
                notify,
                f"⚠️ Не удалось скачать видео по ссылке из письма "
                f"(№<code>{mid}</code>). Повторяю…",
            )
            notified_download_fail = True

        await asyncio.sleep(max(30, poll_sec))

    return None
