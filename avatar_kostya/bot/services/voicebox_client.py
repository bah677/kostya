"""HTTP-клиент Voicebox (клонирование голоса / TTS на GPU)."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from config import config

logger = logging.getLogger(__name__)


class VoiceboxError(RuntimeError):
    """Ошибка API Voicebox."""


class VoiceboxClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = (base_url or config.VOICEBOX_BASE_URL or "").rstrip("/")
        self._timeout = httpx.Timeout(timeout, connect=15.0)

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _url(self, path: str) -> str:
        if not self.base_url:
            raise VoiceboxError("VOICEBOX_BASE_URL не задан")
        return f"{self.base_url}{path}"

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.get(self._url("/health"))
            r.raise_for_status()
            return r.json()

    async def list_profiles(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.get(self._url("/profiles"))
            r.raise_for_status()
            data = r.json()
            return list(data) if isinstance(data, list) else []

    async def create_profile(
        self,
        name: str,
        *,
        language: str = "ru",
        description: str = "",
        default_engine: str = "qwen",
    ) -> dict[str, Any]:
        payload = {
            "name": name.strip(),
            "language": language,
            "voice_type": "cloned",
            "default_engine": default_engine,
        }
        if description:
            payload["description"] = description
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(self._url("/profiles"), json=payload)
            if r.status_code >= 400:
                raise VoiceboxError(f"create profile HTTP {r.status_code}: {r.text[:400]}")
            return r.json()

    async def add_sample(
        self,
        profile_id: str,
        audio_bytes: bytes,
        filename: str,
        reference_text: str,
    ) -> dict[str, Any]:
        files = {"file": (filename, audio_bytes, "application/octet-stream")}
        data = {"reference_text": reference_text.strip()}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                self._url(f"/profiles/{profile_id}/samples"),
                files=files,
                data=data,
            )
            if r.status_code >= 400:
                raise VoiceboxError(f"add sample HTTP {r.status_code}: {r.text[:400]}")
            return r.json()

    async def generate(
        self,
        profile_id: str,
        text: str,
        *,
        language: str = "ru",
        engine: str = "qwen",
        model_size: str = "1.7B",
    ) -> dict[str, Any]:
        payload = {
            "profile_id": profile_id,
            "text": text.strip(),
            "language": language,
            "engine": engine,
            "model_size": model_size,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(self._url("/generate"), json=payload)
            if r.status_code >= 400:
                raise VoiceboxError(f"generate HTTP {r.status_code}: {r.text[:400]}")
            return r.json()

    async def get_generation(self, generation_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.get(self._url(f"/history/{generation_id}"))
            if r.status_code >= 400:
                raise VoiceboxError(f"history HTTP {r.status_code}: {r.text[:400]}")
            return r.json()

    async def wait_generation(
        self,
        generation_id: str,
        *,
        timeout_sec: float = 300.0,
        poll_sec: float = 1.5,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            data = await self.get_generation(generation_id)
            status = (data.get("status") or "").lower()
            if status == "completed":
                return data
            if status in {"failed", "error", "cancelled"}:
                raise VoiceboxError(
                    f"генерация {status}: {data.get('error') or 'без деталей'}"
                )
            await asyncio.sleep(poll_sec)
        raise VoiceboxError(f"таймаут ожидания генерации {generation_id}")

    async def download_audio(self, generation_id: str) -> bytes:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.get(self._url(f"/audio/{generation_id}"))
            if r.status_code >= 400:
                raise VoiceboxError(f"audio HTTP {r.status_code}: {r.text[:400]}")
            return r.content

    async def synthesize_ogg(
        self,
        profile_id: str,
        text: str,
        *,
        language: Optional[str] = None,
        engine: Optional[str] = None,
        model_size: Optional[str] = None,
        work_dir: Optional[Path] = None,
    ) -> Path:
        """Сгенерировать речь и вернуть путь к OGG Opus для Telegram voice."""
        gen = await self.generate(
            profile_id,
            text,
            language=language or config.VOICEBOX_LANGUAGE or "ru",
            engine=engine or config.VOICEBOX_ENGINE or "qwen",
            model_size=model_size or config.VOICEBOX_MODEL_SIZE or "1.7B",
        )
        gid = gen.get("id")
        if not gid:
            raise VoiceboxError(f"нет id в ответе generate: {gen}")
        if (gen.get("status") or "").lower() != "completed":
            await self.wait_generation(gid)
        wav_bytes = await self.download_audio(gid)

        root = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="voicebox_"))
        root.mkdir(parents=True, exist_ok=True)
        wav_path = root / f"{gid}.wav"
        ogg_path = root / f"{gid}.ogg"
        wav_path.write_bytes(wav_bytes)
        ok = await asyncio.to_thread(_wav_to_ogg_opus, wav_path, ogg_path)
        if not ok:
            raise VoiceboxError("ffmpeg не смог сконвертировать WAV → OGG")
        return ogg_path


def _wav_to_ogg_opus(wav_path: Path, ogg_path: Path) -> bool:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(wav_path),
        "-vn",
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
            cmd, capture_output=True, text=True, timeout=120, check=False
        )
        if proc.returncode != 0:
            logger.error("ffmpeg voicebox: %s", (proc.stderr or "")[-500:])
            return False
        return ogg_path.is_file() and ogg_path.stat().st_size > 200
    except Exception as e:
        logger.exception("ffmpeg voicebox convert: %s", e)
        return False
