"""ElevenLabs TTS → OGG Opus (для админ-сравнения)."""

from __future__ import annotations

import asyncio
import logging

import aiohttp

from bot.services.prayer_ssml import prepare_prayer_for_engine
from bot.services.prayer_tts_style import audio_bytes_to_ogg_opus, resolve_prayer_tts_atempo
from config import config

logger = logging.getLogger(__name__)

_MAX_CHARS = 5000
_TIMEOUT_SEC = 120.0


class ElevenLabsTTS:
    def __init__(self) -> None:
        self.api_key = (getattr(config, "ELEVENLABS_API_KEY", None) or "").strip()
        self.voice_id = (getattr(config, "ELEVENLABS_VOICE_ID", None) or "").strip()
        self.model_id = (
            getattr(config, "ELEVENLABS_MODEL_ID", None) or "eleven_multilingual_v2"
        ).strip()
        self.output_format = (
            getattr(config, "ELEVENLABS_OUTPUT_FORMAT", None) or "mp3_44100_128"
        ).strip()
        self.stability = float(getattr(config, "ELEVENLABS_STABILITY", 0.45) or 0.45)
        self.similarity = float(getattr(config, "ELEVENLABS_SIMILARITY", 0.75) or 0.75)
        self.atempo = resolve_prayer_tts_atempo()

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.voice_id)

    async def synthesize_ogg_opus(self, text: str) -> bytes:
        if not self.configured:
            raise RuntimeError("ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID не заданы")

        body, mode = prepare_prayer_for_engine(text, engine="elevenlabs")
        if not body:
            raise ValueError("empty text")
        if len(body) > _MAX_CHARS:
            body = body[:_MAX_CHARS]

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}"
        params = {"output_format": self.output_format}
        payload = {
            "text": body,
            "model_id": self.model_id,
            "voice_settings": {
                "stability": self.stability,
                "similarity_boost": self.similarity,
            },
        }
        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }

        logger.info(
            "ElevenLabs TTS start voice=%s model=%s chars=%s mode=%s",
            self.voice_id[:8],
            self.model_id,
            len(body),
            mode,
        )

        timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SEC)

        async def _request() -> bytes:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url, params=params, headers=headers, json=payload
                ) as resp:
                    raw = await resp.read()
                    if resp.status >= 400:
                        err = raw.decode("utf-8", errors="replace")[:500]
                        raise RuntimeError(f"ElevenLabs HTTP {resp.status}: {err}")
                    if not raw:
                        raise RuntimeError("ElevenLabs вернул пустой ответ")
                    return raw

        audio = await asyncio.wait_for(_request(), timeout=_TIMEOUT_SEC)
        ogg = await asyncio.to_thread(
            audio_bytes_to_ogg_opus, audio, atempo=self.atempo, prefix="elabs_"
        )
        if not ogg:
            raise RuntimeError("ffmpeg не смог обработать ElevenLabs audio")
        logger.info(
            "ElevenLabs TTS ok bytes=%s atempo=%.3f",
            len(ogg),
            self.atempo,
        )
        return ogg
