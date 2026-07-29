"""ElevenLabs TTS → OGG Opus (для админ-сравнения)."""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

import aiohttp

from bot.services.prayer_ssml import prepare_prayer_for_engine
from bot.services.prayer_tts_style import audio_bytes_to_ogg_opus
from config import config

logger = logging.getLogger(__name__)

_MAX_CHARS = 5000
_TIMEOUT_SEC = 120.0

_DEFAULT_COMPARE_VOICES = (
    "CritVAMVzFsSIWmMDe7v",
    "TU2w9J6yEyVkPB7HKH2g",
    "ogi2DyUAKJb7CEdqqvlU",
    "gMIlPNegT3C1SdNBp6rW",
)


def _split_voice_ids(raw: str) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for part in (raw or "").replace(";", ",").split(","):
        vid = part.strip()
        if not vid or vid in seen:
            continue
        seen.add(vid)
        out.append(vid)
    return out


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
        compare_raw = getattr(config, "ELEVENLABS_COMPARE_VOICE_IDS", None)
        if compare_raw is None or not str(compare_raw).strip():
            compare_raw = ",".join(_DEFAULT_COMPARE_VOICES)
        self.compare_voice_ids = _split_voice_ids(str(compare_raw))

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.compare_voice_ids_all)

    @property
    def compare_voice_ids_all(self) -> List[str]:
        """Основной голос + доп. для сравнения (без дублей)."""
        ids: List[str] = []
        seen: set[str] = set()
        for vid in ([self.voice_id] if self.voice_id else []) + self.compare_voice_ids:
            if not vid or vid in seen:
                continue
            seen.add(vid)
            ids.append(vid)
        return ids

    async def synthesize_ogg_opus(
        self, text: str, *, voice_id: Optional[str] = None
    ) -> bytes:
        if not self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY не задан")
        vid = (voice_id or self.voice_id or "").strip()
        if not vid:
            raise RuntimeError("ELEVENLABS_VOICE_ID не задан")

        body, mode = prepare_prayer_for_engine(text, engine="elevenlabs")
        if not body:
            raise ValueError("empty text")
        if len(body) > _MAX_CHARS:
            body = body[:_MAX_CHARS]

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}"
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
            vid[:8],
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
        # Основной голос (#1, ELEVENLABS_VOICE_ID) — ускорение на 10%.
        primary = (self.voice_id or "").strip()
        atempo = 1.1 if primary and vid == primary else 1.0
        ogg = await asyncio.to_thread(
            audio_bytes_to_ogg_opus, audio, atempo=atempo, prefix="elabs_"
        )
        if not ogg:
            raise RuntimeError("ffmpeg не смог обработать ElevenLabs audio")
        logger.info(
            "ElevenLabs TTS ok voice=%s bytes=%s atempo=%.2f",
            vid[:8],
            len(ogg),
            atempo,
        )
        return ogg
