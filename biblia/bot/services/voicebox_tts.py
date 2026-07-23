"""Voicebox TTS для молитв: instruct + лёгкое замедление (atempo) → OGG Opus."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from config import config

logger = logging.getLogger(__name__)

_DEFAULT_INSTRUCT = (
    "Speak slowly and calmly in a soft prayerful tone. "
    "Make clear, unhurried pauses between sentences. Do not rush. "
    "The final word амИнь must be pronounced with stress on the capital И "
    "(second syllable: a-MÍN), clearly and solemnly, never flat or rushed."
)

# Ударение через заглавную гласную: амИнь (а-мИнь).
_AMEN_STRESSED = "амИнь"
_AMEN_FLEX_RE = re.compile(
    r"(?iu)\bа[\u0300\u0301\u0341]?м[\u0300\u0301\u0341]?и[\u0300\u0301\u0341]?"
    r"н[\u0300\u0301\u0341]?ь\b"
)



class VoiceboxError(RuntimeError):
    """Ошибка Voicebox TTS."""


class VoiceboxPrayerTTS:
    """Озвучка молитв через Voicebox (клон голоса на GPU)."""

    def __init__(self) -> None:
        self.base_url = (config.VOICEBOX_BASE_URL or "").rstrip("/")
        self.profile_id = (config.VOICEBOX_PROFILE_ID or "").strip()
        self.engine = (config.VOICEBOX_ENGINE or "qwen").strip() or "qwen"
        self.model_size = (config.VOICEBOX_MODEL_SIZE or "1.7B").strip() or "1.7B"
        self.language = (config.VOICEBOX_LANGUAGE or "ru").strip() or "ru"
        self.instruct = (config.VOICEBOX_INSTRUCT or _DEFAULT_INSTRUCT).strip()
        self.atempo = float(config.VOICEBOX_ATEMPO or 0.92)
        self._timeout = httpx.Timeout(180.0, connect=15.0)

    @property
    def configured(self) -> bool:
        return bool(
            getattr(config, "VOICEBOX_ENABLED", False)
            and self.base_url
            and self.profile_id
        )

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    async def synthesize_ogg_opus(self, text: str) -> bytes:
        if not self.configured:
            raise RuntimeError("Voicebox не настроен (VOICEBOX_*)")

        body = format_prayer_for_tts(text)
        if not body:
            raise ValueError("empty text")

        logger.info(
            "Voicebox TTS start profile=%s chars=%s atempo=%.3f",
            self.profile_id[:8],
            len(body),
            self.atempo,
        )

        gen = await self._generate(body)
        gid = gen.get("id")
        if not gid:
            raise VoiceboxError(f"нет id в ответе generate: {gen}")
        if (gen.get("status") or "").lower() != "completed":
            await self._wait_generation(gid)
        wav_bytes = await self._download_audio(gid)

        ogg = await asyncio.to_thread(
            _wav_bytes_to_ogg_opus, wav_bytes, self.atempo
        )
        if not ogg:
            raise VoiceboxError("ffmpeg не смог сконвертировать WAV → OGG")
        logger.info(
            "Voicebox TTS ok profile=%s chars=%s bytes=%s",
            self.profile_id[:8],
            len(body),
            len(ogg),
        )
        return ogg

    async def _generate(self, text: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "profile_id": self.profile_id,
            "text": text,
            "language": self.language,
            "engine": self.engine,
            "model_size": self.model_size,
            "normalize": True,
        }
        if self.instruct:
            payload["instruct"] = self.instruct
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(self._url("/generate"), json=payload)
            if r.status_code >= 400:
                raise VoiceboxError(f"generate HTTP {r.status_code}: {r.text[:400]}")
            return r.json()

    async def _get_generation(self, generation_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.get(self._url(f"/history/{generation_id}"))
            if r.status_code >= 400:
                raise VoiceboxError(f"history HTTP {r.status_code}: {r.text[:400]}")
            return r.json()

    async def _wait_generation(
        self,
        generation_id: str,
        *,
        timeout_sec: float = 300.0,
        poll_sec: float = 1.5,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            data = await self._get_generation(generation_id)
            status = (data.get("status") or "").lower()
            if status == "completed":
                return data
            if status in {"failed", "error", "cancelled"}:
                raise VoiceboxError(
                    f"генерация {status}: {data.get('error') or 'без деталей'}"
                )
            await asyncio.sleep(poll_sec)
        raise VoiceboxError(f"таймаут ожидания генерации {generation_id}")

    async def _download_audio(self, generation_id: str) -> bytes:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.get(self._url(f"/audio/{generation_id}"))
            if r.status_code >= 400:
                raise VoiceboxError(f"audio HTTP {r.status_code}: {r.text[:400]}")
            return r.content


def format_prayer_for_tts(text: str) -> str:
    """Нормализовать текст молитвы под паузы и ударение «амИнь»."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:\w+)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    t = t.strip().strip('"').strip("«»")
    t = re.sub(r"\+(?=[аАеЕёЁиИоОуУыЫэЭюЮяЯ])", "", t)
    t = re.sub(r"[\u0300\u0301\u0341]", "", t)

    # Уже с пустыми строками между предложениями — слегка подчистить.
    if "\n\n" in t:
        parts = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
        t = "\n\n".join(parts)
    else:
        flat = re.sub(r"[ \t]+", " ", t)
        flat = re.sub(r"\n+", " ", flat).strip()
        parts = re.split(r"(?<=[.!?…])\s+", flat)
        parts = [p.strip() for p in parts if p.strip()]
        t = "\n\n".join(parts) if len(parts) > 1 else flat

    return ensure_amen_stress(t)


def ensure_amen_stress(text: str) -> str:
    """Любое «аминь»/«Аминь» → «амИнь» (ударение заглавной И)."""
    return _AMEN_FLEX_RE.sub(_AMEN_STRESSED, text or "")


def _wav_bytes_to_ogg_opus(wav_bytes: bytes, atempo: float) -> Optional[bytes]:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    tempo = max(0.5, min(1.2, float(atempo or 1.0)))
    with tempfile.TemporaryDirectory(prefix="vb_prayer_") as tmp:
        root = Path(tmp)
        wav_path = root / "in.wav"
        ogg_path = root / "out.ogg"
        wav_path.write_bytes(wav_bytes)
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(wav_path),
            "-vn",
            "-filter:a",
            f"atempo={tempo:.4f}",
            "-c:a",
            "libopus",
            "-b:a",
            "64k",
            "-vbr",
            "on",
            "-application",
            "voip",
            str(ogg_path),
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=180, check=False
            )
            if proc.returncode != 0:
                logger.error("ffmpeg voicebox prayer: %s", (proc.stderr or "")[-500:])
                return None
            if not ogg_path.is_file() or ogg_path.stat().st_size < 200:
                return None
            return ogg_path.read_bytes()
        except Exception as e:
            logger.exception("ffmpeg voicebox prayer convert: %s", e)
            return None
