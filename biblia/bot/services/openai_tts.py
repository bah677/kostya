"""OpenAI TTS для внутреннего сравнения озвучки молитв → OGG Opus."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI

from config import config

logger = logging.getLogger(__name__)

_DEFAULT_INSTRUCT = (
    "Warm natural prayerful speech in Russian, gentle rhythm, slight emotional "
    "variation, not monotone and not robotic. Soft unhurried pace. "
    "The final word амИнь: stress on И (a-MÍN), clear and solemn."
)
_MAX_CHARS = 4096


class OpenAIPrayerTTS:
    """Озвучка через OpenAI audio.speech (тест сравнения для админов)."""

    def __init__(self) -> None:
        self.api_key = (config.OPENAI_API_KEY or "").strip()
        self.model = (getattr(config, "OPENAI_TTS_MODEL", None) or "gpt-4o-mini-tts").strip()
        self.voice = (getattr(config, "OPENAI_TTS_VOICE", None) or "onyx").strip().lower()
        self.instructions = (
            getattr(config, "OPENAI_TTS_INSTRUCT", None) or _DEFAULT_INSTRUCT
        ).strip()
        self.speed = float(getattr(config, "OPENAI_TTS_SPEED", 1.0) or 1.0)
        self._client: Optional[AsyncOpenAI] = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _client_or_raise(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(api_key=self.api_key)
        return self._client

    async def synthesize_ogg_opus(self, text: str) -> bytes:
        if not self.configured:
            raise RuntimeError("OPENAI_API_KEY не задан")

        body = (text or "").strip()
        if not body:
            raise ValueError("empty text")
        if len(body) > _MAX_CHARS:
            body = body[:_MAX_CHARS]

        logger.info(
            "OpenAI TTS start model=%s voice=%s chars=%s",
            self.model,
            self.voice,
            len(body),
        )

        kwargs: dict = {
            "model": self.model,
            "voice": self.voice,
            "input": body,
            "response_format": "wav",
        }
        # instructions поддерживает gpt-4o-mini-tts*; speed — в основном tts-1*
        if self.model.startswith("gpt-4o-mini-tts") and self.instructions:
            kwargs["instructions"] = self.instructions
        if self.model.startswith("tts-") and self.speed and abs(self.speed - 1.0) > 0.01:
            kwargs["speed"] = max(0.25, min(4.0, self.speed))

        client = self._client_or_raise()
        resp = await client.audio.speech.create(**kwargs)
        if hasattr(resp, "aread"):
            wav_bytes = await resp.aread()
        else:
            wav_bytes = resp.content
        if not wav_bytes:
            raise RuntimeError("OpenAI TTS вернул пустой ответ")

        ogg = await asyncio.to_thread(_wav_bytes_to_ogg_opus, wav_bytes)
        if not ogg:
            raise RuntimeError("ffmpeg не смог сконвертировать OpenAI WAV → OGG")
        logger.info(
            "OpenAI TTS ok model=%s voice=%s bytes=%s",
            self.model,
            self.voice,
            len(ogg),
        )
        return ogg


def _wav_bytes_to_ogg_opus(wav_bytes: bytes) -> Optional[bytes]:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    with tempfile.TemporaryDirectory(prefix="oai_prayer_") as tmp:
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
            "-c:a",
            "libopus",
            "-b:a",
            "96k",
            "-vbr",
            "on",
            "-application",
            "audio",
            str(ogg_path),
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=180, check=False
            )
            if proc.returncode != 0:
                logger.error("ffmpeg openai prayer: %s", (proc.stderr or "")[-500:])
                return None
            if not ogg_path.is_file() or ogg_path.stat().st_size < 200:
                return None
            return ogg_path.read_bytes()
        except Exception as e:
            logger.exception("ffmpeg openai prayer convert: %s", e)
            return None
