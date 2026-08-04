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


# Паузы для ElevenLabs (plain): замедляем тишиной между фразами, не растягиванием слов.
_LONG_SENT_WORDS = 20
_PAIR_MAX_WORDS = 16
_BREATH_GAP_RE = re.compile(
    r"(?i)(?:,\s+|\s+(?:чтобы|ибо|потому что|когда|если)\s+)"
)


def _word_count(s: str) -> int:
    return len([w for w in (s or "").split() if w])


def _insert_breath_in_long_sentence(sent: str) -> str:
    """В длинной фразе — мягкий выдох через тире (ElevenLabs обычно чуть тормозит на —)."""
    s = (sent or "").strip()
    if _word_count(s) < _LONG_SENT_WORDS:
        return s
    if " — " in s or " – " in s:
        return s
    mid = len(s) // 2
    best: int | None = None
    best_dist = 10**9
    for m in _BREATH_GAP_RE.finditer(s):
        # не режем слишком близко к краям
        if m.start() < 12 or m.end() > len(s) - 12:
            continue
        dist = abs(m.start() - mid)
        if dist < best_dist:
            best_dist = dist
            best = m.start()
    if best is None:
        return s
    left = s[:best].rstrip(" ,")
    right = s[best:].lstrip(" ,")
    if not left or not right:
        return s
    return f"{left} — {right}"


def _pack_prayer_paragraphs(sentences: list[str]) -> list[str]:
    """
    Умная группировка для озвучки:
    - короткие фразы можно склеить по 2 в абзац (лёгкая связка);
    - длинные — отдельно (пауза \\n\\n перед/после);
    - внутри длинных — дыхание через —.
    """
    paras: list[str] = []
    buf: list[str] = []
    buf_words = 0

    def flush() -> None:
        nonlocal buf, buf_words
        if not buf:
            return
        paras.append(" ".join(buf).strip())
        buf = []
        buf_words = 0

    for raw in sentences:
        s = _insert_breath_in_long_sentence(raw.strip())
        if not s:
            continue
        w = _word_count(s)
        # длинная фраза всегда своим абзацем
        if w >= _LONG_SENT_WORDS:
            flush()
            paras.append(s)
            continue
        if buf and (buf_words + w > _PAIR_MAX_WORDS or len(buf) >= 2):
            flush()
        buf.append(s)
        buf_words += w
        if len(buf) >= 2 or buf_words >= _PAIR_MAX_WORDS:
            flush()
    flush()
    return paras


def format_prayer_for_tts(text: str) -> str:
    """Нормализовать текст под ElevenLabs: смысловые паузы (абзацы/тире) + амИнь."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:\w+)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    t = t.strip().strip('"').strip("«»")
    t = re.sub(r"\+(?=[аАеЕёЁиИоОуУыЫэЭюЮяЯ])", "", t)
    t = re.sub(r"[\u0300\u0301\u0341]", "", t)
    # убрать наши прошлые маркеры пауз, чтобы повторный format был идемпотентным
    t = t.replace("…", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)

    if "\n\n" in t:
        raw_parts = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    else:
        flat = re.sub(r"\n+", " ", t).strip()
        raw_parts = [flat] if flat else []

    sentences: list[str] = []
    for part in raw_parts:
        bits = re.split(r"(?<=[.!?])\s+", part)
        sentences.extend(b.strip() for b in bits if b.strip())

    amen = None
    if sentences and _AMEN_FLEX_RE.search(sentences[-1]):
        amen = ensure_amen_stress(sentences[-1])
        sentences = sentences[:-1]

    paras = _pack_prayer_paragraphs(sentences)
    out = "\n\n".join(paras)
    if amen:
        # отдельный абзац перед амИнь — торжественная пауза
        out = f"{out}\n\n{amen}".strip() if out else amen
    return ensure_amen_stress(out) if out else ""


def ensure_amen_stress(text: str) -> str:
    """Любое «аминь»/«Аминь» → «амИнь» (ударение заглавной И)."""
    return _AMEN_FLEX_RE.sub(_AMEN_STRESSED, text or "")


# backward-compat alias (раньше локальный helper)
def _wav_bytes_to_ogg_opus(wav_bytes: bytes, atempo: float) -> Optional[bytes]:
    from bot.services.prayer_tts_style import audio_bytes_to_ogg_opus

    return audio_bytes_to_ogg_opus(wav_bytes, atempo=atempo, prefix="vb_prayer_")
