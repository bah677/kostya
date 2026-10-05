"""Дубль Shorts-молитв голосовыми в TG-канал с отложенной публикацией.

Bot API не умеет schedule_date — очередь на диске + фоновый поллер.
Слот публикации тот же, что у YouTube-премьеры (premiere_schedule).
Голос: нативный sendVoice (OGG Opus), с ужатием под лимит волны 1 МиБ.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from aiogram.types import FSInputFile

from telemost_audio.telegram_voice_opus import (
    ffmpeg_bin,
    libopus_voice_args,
    ogg_path_duration_sec,
    opus_bitrate_for_tg_waveform,
    probe_media_duration_sec,
    shrink_ogg_under_limit,
)
from telemost_audio.tg_voice_delivery import ensure_under_waveform_limit
from youtube_prayer.premiere_schedule import (
    premiere_slot_datetime,
    premiere_slot_label,
)
from youtube_shorts.uploader import shorts_premiere_hours_msk

logger = logging.getLogger(__name__)
_MSK = ZoneInfo("Europe/Moscow")

_DEFAULT_CTA = "Если молитва про тебя — напиши «Аминь» в комментариях."


def _cfg(name: str, default=None):
    try:
        from config import config as cfg

        return getattr(cfg, name, default)
    except Exception:
        return default


def tg_channel_publish_enabled() -> bool:
    return bool(_cfg("YT_SHORTS_TG_CHANNEL_ENABLED", True))


def tg_channel_chat_id() -> int:
    return int(_cfg("YT_SHORTS_TG_CHANNEL_ID", 0) or 0)


def tg_queue_root(work_root: Path) -> Path:
    return Path(work_root) / "tg_voice_queue"


def _caption(*, title: str, trend: str = "") -> str:
    head = (title or trend or "Молитва").strip()
    # Коротко: тема + призыв к комментарию (как на YouTube).
    return f"{head}\n\n{_DEFAULT_CTA}"


def _encode_voice_ogg(src: Path, dst: Path) -> bool:
    """WAV/OGG/MP3 → OGG Opus voip под sendVoice."""
    if not src.is_file():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dur = probe_media_duration_sec(src) or 90.0
    bitrate = opus_bitrate_for_tg_waveform(dur)
    cmd = [
        ffmpeg_bin(),
        "-y",
        "-i",
        str(src),
        "-vn",
        *libopus_voice_args(bitrate=bitrate),
        str(dst),
    ]
    try:
        import subprocess

        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, check=False
        )
        if proc.returncode != 0 or not dst.is_file() or dst.stat().st_size < 200:
            logger.error(
                "tg voice encode failed: %s", (proc.stderr or "")[-400:]
            )
            return False
    except Exception as e:
        logger.exception("tg voice encode: %s", e)
        return False
    ensure_under_waveform_limit(dst)
    if dst.stat().st_size > 1024 * 1024:
        shrunk = dst.with_name(dst.stem + "_1m.ogg")
        if shrink_ogg_under_limit(
            dst, shrunk, duration_sec=probe_media_duration_sec(dst) or dur
        ):
            if shrunk.is_file():
                shrunk.replace(dst)
    return dst.is_file() and dst.stat().st_size > 200


def enqueue_short_voice(
    *,
    work_root: Path,
    item_dir: Path,
    day: str,
    index: int,
    title: str,
    trend: str = "",
    audio_src: Optional[Path] = None,
    publish_at_msk: Optional[datetime] = None,
) -> Optional[Path]:
    """Готовит voice.ogg + job.json в очереди. Возвращает путь к job-директории."""
    if not tg_channel_publish_enabled():
        return None
    chat_id = tg_channel_chat_id()
    if not chat_id:
        logger.warning("YT_SHORTS_TG_CHANNEL_ID не задан — голос в канал пропущен")
        return None

    src = audio_src
    if src is None or not src.is_file():
        for cand in (
            item_dir / "prayer_mixed.wav",
            item_dir / "prayer_mixed.ogg",
        ):
            if cand.is_file():
                src = cand
                break
    if src is None or not src.is_file():
        logger.error("tg voice: нет аудио в %s", item_dir)
        return None

    if publish_at_msk is None:
        publish_at_msk = premiere_slot_datetime(
            day=day,
            index=index,
            slots_msk=shorts_premiere_hours_msk(),
        )

    job_id = f"{day}_{int(index):02d}"
    job_dir = tg_queue_root(work_root) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    voice = job_dir / "voice.ogg"
    if not _encode_voice_ogg(src, voice):
        return None

    meta = {
        "day": day,
        "index": int(index),
        "chat_id": chat_id,
        "title": (title or "")[:200],
        "trend": (trend or "")[:200],
        "caption": _caption(title=title, trend=trend),
        "publish_at_msk": publish_at_msk.astimezone(_MSK).isoformat(),
        "premiere_label": premiere_slot_label(publish_at_msk),
        "status": "pending",
        "voice": "voice.ogg",
        "source_item": str(item_dir),
        "enqueued_at": datetime.now(_MSK).isoformat(),
    }
    (job_dir / "job.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        "tg voice queued %s slot=%s chat=%s",
        job_id,
        meta["premiere_label"],
        chat_id,
    )
    return job_dir


async def publish_due_tg_voices(
    bot: Any,
    *,
    work_root: Path,
    now: Optional[datetime] = None,
    limit: int = 20,
) -> int:
    """Отправляет просроченные/готовые голосовые из очереди. Возвращает число отправок."""
    if not tg_channel_publish_enabled():
        return 0
    root = tg_queue_root(work_root)
    if not root.is_dir():
        return 0
    now_msk = (now or datetime.now(_MSK)).astimezone(_MSK)
    sent = 0

    jobs = sorted(root.iterdir(), key=lambda p: p.name)
    for job_dir in jobs:
        if sent >= limit:
            break
        if not job_dir.is_dir():
            continue
        meta_path = job_dir / "job.json"
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(meta.get("status") or "") != "pending":
            continue
        try:
            when = datetime.fromisoformat(str(meta["publish_at_msk"]))
            if when.tzinfo is None:
                when = when.replace(tzinfo=_MSK)
            when = when.astimezone(_MSK)
        except Exception:
            logger.error("tg voice bad publish_at in %s", job_dir)
            continue
        if when > now_msk:
            continue

        voice = job_dir / str(meta.get("voice") or "voice.ogg")
        if not voice.is_file():
            meta["status"] = "error"
            meta["error"] = "voice.ogg missing"
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            continue

        chat_id = int(meta.get("chat_id") or tg_channel_chat_id() or 0)
        if not chat_id:
            continue

        caption = str(meta.get("caption") or _caption(title=str(meta.get("title") or "")))
        # TG caption limit 1024
        caption = caption[:1024]
        dur = ogg_path_duration_sec(voice)
        try:
            kwargs: dict = {"caption": caption}
            if dur is not None:
                kwargs["duration"] = dur
            msg = await bot.send_voice(
                chat_id,
                FSInputFile(str(voice), filename="prayer.ogg"),
                **kwargs,
            )
            meta["status"] = "sent"
            meta["sent_at"] = datetime.now(_MSK).isoformat()
            meta["message_id"] = getattr(msg, "message_id", None)
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            sent += 1
            logger.info(
                "tg voice sent job=%s msg=%s slot=%s",
                job_dir.name,
                meta.get("message_id"),
                meta.get("premiere_label"),
            )
            # После отправки голос можно убрать — job.json оставляем для идемпотентности.
            try:
                voice.unlink(missing_ok=True)
            except Exception:
                pass
        except Exception as e:
            logger.exception("tg voice send failed %s: %s", job_dir.name, e)
            meta["last_error"] = str(e)[:500]
            meta["last_error_at"] = datetime.now(_MSK).isoformat()
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            # не помечаем error сразу — повторим на следующем тике
    return sent


async def tg_voice_poll_loop(
    bot: Any,
    *,
    work_root: Path,
    interval_sec: float = 30.0,
) -> None:
    """Фон: раз в interval_sec публикует due голосовые."""
    root = Path(work_root)
    while True:
        try:
            n = await publish_due_tg_voices(bot, work_root=root)
            if n:
                logger.info("tg voice poll: sent %s", n)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("tg voice poll: %s", e)
        await asyncio.sleep(interval_sec)


def copy_audio_aside(src: Path, dest: Path) -> Optional[Path]:
    """На случай, если cleanup снесёт исходник до кодирования."""
    try:
        if not src.is_file():
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return dest
    except Exception as e:
        logger.warning("copy_audio_aside: %s", e)
        return None
