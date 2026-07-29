"""OpenAI TTS для внутреннего сравнения озвучки молитв → OGG Opus."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from openai import AsyncOpenAI

from bot.services.prayer_ssml import (
    prepare_prayer_for_engine,
    resolve_prayer_tts_instruct_with_ssml,
)
from bot.services.prayer_tts_style import audio_bytes_to_ogg_opus
from config import config

logger = logging.getLogger(__name__)

_MAX_CHARS = 4096


class OpenAIPrayerTTS:
    """Озвучка через OpenAI audio.speech (тест сравнения для админов)."""

    def __init__(self) -> None:
        self.api_key = (config.OPENAI_API_KEY or "").strip()
        self.model = (getattr(config, "OPENAI_TTS_MODEL", None) or "gpt-4o-mini-tts").strip()
        self.voice = (getattr(config, "OPENAI_TTS_VOICE", None) or "onyx").strip().lower()
        self.instructions = resolve_prayer_tts_instruct_with_ssml()
        self.speed = 1.0
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

        body, mode = prepare_prayer_for_engine(text, engine="openai")
        if not body:
            raise ValueError("empty text")
        if len(body) > _MAX_CHARS:
            body = body[:_MAX_CHARS]

        logger.info(
            "OpenAI TTS start model=%s voice=%s chars=%s mode=%s",
            self.model,
            self.voice,
            len(body),
            mode,
        )

        kwargs: dict = {
            "model": self.model,
            "voice": self.voice,
            "input": body,
            "response_format": "wav",
        }
        if self.model.startswith("gpt-4o-mini-tts") and self.instructions:
            kwargs["instructions"] = self.instructions
        if self.model.startswith("tts-"):
            kwargs["speed"] = 1.0

        client = self._client_or_raise()
        resp = await client.audio.speech.create(**kwargs)
        if hasattr(resp, "aread"):
            wav_bytes = await resp.aread()
        else:
            wav_bytes = resp.content
        if not wav_bytes:
            raise RuntimeError("OpenAI TTS вернул пустой ответ")

        # Только WAV→OGG для Telegram, без замедления (atempo).
        ogg = await asyncio.to_thread(
            audio_bytes_to_ogg_opus,
            wav_bytes,
            atempo=1.0,
            prefix="oai_prayer_",
        )
        if not ogg:
            raise RuntimeError("ffmpeg не смог сконвертировать OpenAI WAV → OGG")
        logger.info(
            "OpenAI TTS ok model=%s voice=%s bytes=%s",
            self.model,
            self.voice,
            len(ogg),
        )
        return ogg
