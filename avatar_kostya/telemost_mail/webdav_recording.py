"""Записи Телемоста на Я.Диске пользователя (WebDAV), если yadi.sk недоступен."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Sequence

from config import config
from yandex_disk.webdav import RemoteFile, YandexDiskWebDAV

logger = logging.getLogger(__name__)

_VIDEO_EXTS = (".webm", ".mp4", ".mkv", ".mov")

# Ниже этого порога audio_only считаем урезанным / ранним → берём звук из видео.
_DEFAULT_MIN_AUDIO_SEC = 180.0


def probe_media_duration_sec(path: str | Path) -> Optional[float]:
    """Длительность файла через ffprobe; None если не удалось."""
    p = Path(path)
    if not p.is_file():
        return None
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(p),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode != 0:
            return None
        return float((proc.stdout or "").strip())
    except Exception:
        return None


def _min_full_audio_sec() -> float:
    return float(
        getattr(config, "TELEMOST_AUDIO_MIN_FULL_DURATION_SEC", _DEFAULT_MIN_AUDIO_SEC)
        or _DEFAULT_MIN_AUDIO_SEC
    )


def audio_duration_is_full(path: str | Path) -> bool:
    dur = probe_media_duration_sec(path)
    if dur is None:
        # Не можем проверить — не считаем «полным», чтобы не залипнуть на битом кэше.
        return False
    return dur >= _min_full_audio_sec()


def _safe_meeting_id(meeting_id: str) -> str:
    return re.sub(r"[^\w\-]+", "_", (meeting_id or "unknown").strip())[:64]


def _webdav_dirs() -> Sequence[str]:
    custom = (getattr(config, "TELEMOST_RECORDINGS_WEBDAV_DIR", "") or "").strip()
    dirs = []
    if custom:
        dirs.append(custom)
    for d in ("/Записи Телемоста", "Записи Телемоста"):
        if d not in dirs:
            dirs.append(d)
    return dirs


def _is_video_recording_file(remote: RemoteFile, meeting_id: str) -> bool:
    name = (remote.name or "").lower()
    path = (remote.path or "").lower()
    if meeting_id not in name and meeting_id not in path:
        return False
    if "audio_only" in name:
        return False
    return any(name.endswith(ext) for ext in _VIDEO_EXTS)


async def find_telemost_video_on_webdav(meeting_id: str) -> Optional[str]:
    """Путь на WebDAV к видеозаписи встречи или None."""
    mid = (meeting_id or "").strip()
    if not mid:
        return None
    login = (getattr(config, "YANDEX_DISK_LOGIN", "") or "").strip()
    password = (getattr(config, "YANDEX_DISK_PASSWORD", "") or "").strip()
    webdav = YandexDiskWebDAV(login, password)
    if not webdav.configured:
        return None

    best: Optional[RemoteFile] = None
    for dir_path in _webdav_dirs():
        try:
            files = await webdav.list_files(dir_path, recursive=True)
        except Exception as e:
            logger.warning("webdav list %s: %s", dir_path, e)
            continue
        for remote in files:
            if not _is_video_recording_file(remote, mid):
                continue
            if best is None or remote.size > best.size:
                best = remote
        if best is not None:
            break

    if best is None:
        return None
    logger.info(
        "webdav recording hit meeting_id=%s path=%s size=%s",
        mid,
        best.path,
        best.size,
    )
    return best.path


_AUDIO_EXTS = (".mp3", ".m4a", ".ogg", ".opus", ".wav")


def _is_audio_recording_file(remote: RemoteFile, meeting_id: str) -> bool:
    name = (remote.name or "").lower()
    path = (remote.path or "").lower()
    if meeting_id not in name and meeting_id not in path:
        return False
    return "audio" in name or any(name.endswith(ext) for ext in _AUDIO_EXTS)


async def find_telemost_audio_on_webdav(meeting_id: str) -> Optional[str]:
    mid = (meeting_id or "").strip()
    if not mid:
        return None
    login = (getattr(config, "YANDEX_DISK_LOGIN", "") or "").strip()
    password = (getattr(config, "YANDEX_DISK_PASSWORD", "") or "").strip()
    webdav = YandexDiskWebDAV(login, password)
    if not webdav.configured:
        return None

    best: Optional[RemoteFile] = None
    for dir_path in _webdav_dirs():
        try:
            files = await webdav.list_files(dir_path, recursive=True)
        except Exception as e:
            logger.warning("webdav list %s: %s", dir_path, e)
            continue
        for remote in files:
            if not _is_audio_recording_file(remote, mid):
                continue
            if best is None or remote.size > best.size:
                best = remote
        if best is not None:
            break

    if best is None:
        return None
    logger.info(
        "webdav audio hit meeting_id=%s path=%s size=%s",
        mid,
        best.path,
        best.size,
    )
    return best.path


async def _extract_audio_from_video_file(
    video_local: Path,
    out_mp3: Path,
) -> bool:
    if not shutil.which("ffmpeg"):
        logger.error("ffmpeg not found — cannot extract audio from video")
        return False

    def _extract() -> bool:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_local),
            "-vn",
            "-acodec",
            "libmp3lame",
            "-q:a",
            "4",
            str(out_mp3),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        return (
            proc.returncode == 0
            and out_mp3.is_file()
            and out_mp3.stat().st_size > 10_000
        )

    return await asyncio.to_thread(_extract)


async def _download_video_for_audio(
    webdav: YandexDiskWebDAV,
    meeting_id: str,
    root: Path,
) -> Optional[Path]:
    video_remote = await find_telemost_video_on_webdav(meeting_id)
    if not video_remote:
        return None
    mid = _safe_meeting_id(meeting_id)
    v_ext = Path(video_remote).suffix.lower() or ".webm"
    video_local = root / f"{mid}_src{v_ext}"
    try:
        if not (video_local.is_file() and video_local.stat().st_size > 10_000):
            await webdav.download(video_remote, str(video_local))
    except Exception as e:
        logger.error("webdav video-for-audio meeting_id=%s: %s", meeting_id, e)
        return None
    if video_local.is_file() and video_local.stat().st_size > 10_000:
        return video_local
    return None


async def download_telemost_audio_webdav(
    meeting_id: str,
    *,
    dest_dir: str | Path,
) -> Optional[str]:
    """Скачивает аудио с Диска.

    Берёт audio_only, но если он короче порога (ранний/урезанный файл Телемоста) —
    вытаскивает звук из полного видео на Диске.
    """
    root = Path(dest_dir)
    root.mkdir(parents=True, exist_ok=True)
    mid = _safe_meeting_id(meeting_id)
    cached_mp3 = root / f"{mid}_audio.mp3"
    if (
        cached_mp3.is_file()
        and cached_mp3.stat().st_size > 10_000
        and audio_duration_is_full(cached_mp3)
    ):
        return str(cached_mp3.resolve())

    login = (getattr(config, "YANDEX_DISK_LOGIN", "") or "").strip()
    password = (getattr(config, "YANDEX_DISK_PASSWORD", "") or "").strip()
    webdav = YandexDiskWebDAV(login, password)
    if not webdav.configured:
        return None

    remote_path = await find_telemost_audio_on_webdav(meeting_id)
    audio_local: Optional[Path] = None
    if remote_path:
        ext = Path(remote_path).suffix.lower() or ".mp3"
        out = root / f"{mid}_audio{ext}"
        need_dl = not (
            out.is_file()
            and out.stat().st_size > 10_000
            and audio_duration_is_full(out)
        )
        if need_dl:
            try:
                await webdav.download(remote_path, str(out))
            except Exception as e:
                logger.error("webdav audio download meeting_id=%s: %s", meeting_id, e)
        if out.is_file() and out.stat().st_size > 10_000:
            audio_local = out
            logger.info("audio downloaded via webdav: %s", out)

    if audio_local is not None and audio_duration_is_full(audio_local):
        return str(audio_local.resolve())

    # Короткий / нет audio_only → звук из видео.
    if audio_local is not None:
        dur = probe_media_duration_sec(audio_local)
        logger.warning(
            "webdav audio_only too short meeting_id=%s duration=%s — extract from video",
            meeting_id,
            f"{dur:.1f}s" if dur is not None else "?",
        )

    video_local = await _download_video_for_audio(webdav, meeting_id, root)
    if not video_local:
        if audio_local is not None:
            logger.warning(
                "webdav: no video to replace short audio, keeping short file meeting_id=%s",
                meeting_id,
            )
            return str(audio_local.resolve())
        return None

    ok = await _extract_audio_from_video_file(video_local, cached_mp3)
    if ok and audio_duration_is_full(cached_mp3):
        logger.info(
            "audio extracted from webdav video meeting_id=%s -> %s",
            meeting_id,
            cached_mp3,
        )
        return str(cached_mp3.resolve())
    if ok:
        dur = probe_media_duration_sec(cached_mp3)
        logger.warning(
            "extracted audio still short meeting_id=%s duration=%s",
            meeting_id,
            f"{dur:.1f}s" if dur is not None else "?",
        )
        return str(cached_mp3.resolve())
    logger.error("ffmpeg extract audio failed meeting_id=%s", meeting_id)
    if audio_local is not None:
        return str(audio_local.resolve())
    return None


async def download_telemost_recording_webdav(
    meeting_id: str,
    *,
    dest_dir: str | Path,
) -> Optional[str]:
    """Скачивает запись с Я.Диска по meeting_id в каталог dest_dir."""
    remote_path = await find_telemost_video_on_webdav(meeting_id)
    if not remote_path:
        return None

    login = (getattr(config, "YANDEX_DISK_LOGIN", "") or "").strip()
    password = (getattr(config, "YANDEX_DISK_PASSWORD", "") or "").strip()
    webdav = YandexDiskWebDAV(login, password)
    if not webdav.configured:
        return None

    root = Path(dest_dir)
    root.mkdir(parents=True, exist_ok=True)
    ext = Path(remote_path).suffix.lower() or ".webm"
    out = root / f"{_safe_meeting_id(meeting_id)}{ext}"
    if out.is_file() and out.stat().st_size > 10_000:
        return str(out.resolve())

    try:
        await webdav.download(remote_path, str(out))
    except Exception as e:
        logger.error("webdav download meeting_id=%s: %s", meeting_id, e)
        return None

    if out.is_file() and out.stat().st_size > 10_000:
        logger.info("recording downloaded via webdav: %s", out)
        return str(out.resolve())
    return None
