"""Синтез речи Yandex SpeechKit (v1 REST) → OGG Opus для Telegram voice."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp

from config import config

logger = logging.getLogger(__name__)

_TTS_URL = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"
_MAX_CHARS = 4800
_TTS_TIMEOUT_SEC = 60.0
# SpeechKit v1: emotion — jane/omazh/ermil/zahar; speed — не для premium (filipp, alena).
_EMOTION_VOICES = frozenset({"jane", "omazh", "ermil", "zahar"})
_NO_SPEED_VOICES = frozenset({"filipp", "alena"})
_DEFAULT_VOICE = "zahar"


class YandexSpeechKitTTS:
    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        folder_id: Optional[str] = None,
        voice: Optional[str] = None,
        speed: Optional[float] = None,
        emotion: Optional[str] = None,
    ) -> None:
        self.api_key = (api_key or config.YANDEX_SPEECHKIT_API_KEY or "").strip()
        self.folder_id = (folder_id or config.YANDEX_CLOUD_FOLDER_ID or "").strip()
        self.voice = (voice or config.YANDEX_SPEECHKIT_VOICE or _DEFAULT_VOICE).strip().lower()
        self.speed = speed if speed is not None else config.YANDEX_SPEECHKIT_SPEED
        self.emotion = (emotion or config.YANDEX_SPEECHKIT_EMOTION or "").strip()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def synthesize_ogg_opus(self, text: str) -> bytes:
        """Голосовое Telegram: OGG + Opus из SpeechKit."""
        if not self.configured:
            raise RuntimeError("YANDEX_SPEECHKIT_API_KEY не задан")

        body = (text or "").strip()
        if not body:
            raise ValueError("empty text")
        if len(body) > _MAX_CHARS:
            body = body[:_MAX_CHARS]

        params = {
            "text": body,
            "lang": "ru-RU",
            "voice": self.voice,
            "format": "oggopus",
        }
        if self.voice not in _NO_SPEED_VOICES:
            params["speed"] = str(self.speed)
        if self.emotion and self.voice in _EMOTION_VOICES:
            params["emotion"] = self.emotion

        headers = {"Authorization": f"Api-Key {self.api_key}"}
        if self.folder_id:
            headers["x-folder-id"] = self.folder_id

        logger.info(
            "SpeechKit TTS request voice=%s chars=%s",
            self.voice,
            len(body),
        )

        async def _request() -> bytes:
            timeout = aiohttp.ClientTimeout(connect=10, sock_read=50, total=55)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(_TTS_URL, headers=headers, data=params) as resp:
                    raw = await resp.read()
                    if resp.status != 200:
                        err = raw.decode("utf-8", errors="replace")[:500]
                        logger.error(
                            "SpeechKit TTS %s voice=%s: %s",
                            resp.status,
                            self.voice,
                            err,
                        )
                        raise RuntimeError(f"SpeechKit HTTP {resp.status}: {err}")
                    if not raw:
                        raise RuntimeError("SpeechKit вернул пустой ответ")
                    return raw

        try:
            raw = await asyncio.wait_for(_request(), timeout=_TTS_TIMEOUT_SEC)
        except asyncio.TimeoutError as e:
            logger.error(
                "SpeechKit TTS timeout voice=%s chars=%s after %.0fs",
                self.voice,
                len(body),
                _TTS_TIMEOUT_SEC,
            )
            raise RuntimeError(f"SpeechKit timeout after {_TTS_TIMEOUT_SEC:.0f}s") from e

        logger.info(
            "SpeechKit TTS ok voice=%s chars=%s bytes=%s",
            self.voice,
            len(body),
            len(raw),
        )
        return raw
