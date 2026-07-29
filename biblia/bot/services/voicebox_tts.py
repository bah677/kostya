"""Voicebox TTS для молитв: instruct + лёгкое замедление (atempo) → OGG Opus."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Optional

import httpx

from config import config

logger = logging.getLogger(__name__)

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
        from bot.services.prayer_tts_style import resolve_prayer_tts_atempo
        from bot.services.prayer_ssml import resolve_prayer_tts_instruct_with_ssml

        self.base_url = (config.VOICEBOX_BASE_URL or "").rstrip("/")
        self.profile_id = (config.VOICEBOX_PROFILE_ID or "").strip()
        self.engine = (config.VOICEBOX_ENGINE or "qwen").strip() or "qwen"
        self.model_size = (config.VOICEBOX_MODEL_SIZE or "1.7B").strip() or "1.7B"
        self.language = (config.VOICEBOX_LANGUAGE or "ru").strip() or "ru"
        # Voicebox instruct: базовый + guidance пауз из SSML-логики.
        self.instruct = resolve_prayer_tts_instruct_with_ssml()
        self.atempo = resolve_prayer_tts_atempo()
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

        from bot.services.prayer_ssml import prepare_prayer_for_engine

        body, mode = prepare_prayer_for_engine(text, engine="voicebox")
        if not body:
            raise ValueError("empty text")

        logger.info(
            "Voicebox TTS start profile=%s chars=%s atempo=%.3f mode=%s",
            self.profile_id[:8],
            len(body),
            self.atempo,
            mode,
        )

        gen = await self._generate(body)
        gid = gen.get("id")
        if not gid:
            raise VoiceboxError(f"нет id в ответе generate: {gen}")
        if (gen.get("status") or "").lower() != "completed":
            await self._wait_generation(gid)
        wav_bytes = await self._download_audio(gid)

        from bot.services.prayer_tts_style import audio_bytes_to_ogg_opus

        ogg = await asyncio.to_thread(
            audio_bytes_to_ogg_opus,
            wav_bytes,
            atempo=self.atempo,
            prefix="vb_prayer_",
        )
        if not ogg:
            raise VoiceboxError("ffmpeg не смог сконвертировать WAV → OGG")
        logger.info(
            "Voicebox TTS ok profile=%s chars=%s bytes=%s atempo=%.3f",
            self.profile_id[:8],
            len(body),
            len(ogg),
            self.atempo,
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
    """Нормализовать текст: мягкие паузы (абзацы по 2 предложения) + амИнь."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:\w+)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    t = t.strip().strip('"').strip("«»")
    t = re.sub(r"\+(?=[аАеЕёЁиИоОуУыЫэЭюЮяЯ])", "", t)
    t = re.sub(r"[\u0300\u0301\u0341]", "", t)

    # Собрать предложения из абзацев / одной строки.
    if "\n\n" in t:
        raw_parts = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    else:
        flat = re.sub(r"[ \t]+", " ", t)
        flat = re.sub(r"\n+", " ", flat).strip()
        raw_parts = [flat] if flat else []

    sentences: list[str] = []
    for part in raw_parts:
        bits = re.split(r"(?<=[.!?…])\s+", part)
        sentences.extend(b.strip() for b in bits if b.strip())

    amen = None
    if sentences and _AMEN_FLEX_RE.search(sentences[-1]):
        amen = ensure_amen_stress(sentences[-1])
        sentences = sentences[:-1]

    # По 2 предложения в абзаце — естественнее, чем пауза после каждого.
    paras: list[str] = []
    for i in range(0, len(sentences), 2):
        chunk = " ".join(sentences[i : i + 2]).strip()
        if chunk:
            paras.append(chunk)
    out = "\n\n".join(paras)
    if amen:
        out = f"{out}\n\n{amen}".strip() if out else amen
    return ensure_amen_stress(out) if out else ""


def ensure_amen_stress(text: str) -> str:
    """Любое «аминь»/«Аминь» → «амИнь» (ударение заглавной И)."""
    return _AMEN_FLEX_RE.sub(_AMEN_STRESSED, text or "")


# backward-compat alias (раньше локальный helper)
def _wav_bytes_to_ogg_opus(wav_bytes: bytes, atempo: float) -> Optional[bytes]:
    from bot.services.prayer_tts_style import audio_bytes_to_ogg_opus

    return audio_bytes_to_ogg_opus(wav_bytes, atempo=atempo, prefix="vb_prayer_")
