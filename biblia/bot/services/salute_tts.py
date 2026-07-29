"""SberDevices SaluteSpeech TTS → OGG Opus (для админ-сравнения)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Optional

import aiohttp

from bot.services.prayer_ssml import prepare_prayer_for_engine
from bot.services.prayer_tts_style import audio_bytes_to_ogg_opus, resolve_prayer_tts_atempo
from config import config

logger = logging.getLogger(__name__)

_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
_SYNTH_URL = "https://smartspeech.sber.ru/rest/v1/text:synthesize"
_MAX_CHARS = 4000
_TIMEOUT_SEC = 90.0
_SCOPE_MISMATCH = "scope from db not fully includes consumed scope"


class SaluteSpeechTTS:
    def __init__(self) -> None:
        self.auth_key = (getattr(config, "SALUTE_SPEECH_AUTH_KEY", None) or "").strip()
        self.enabled = bool(getattr(config, "SALUTE_SPEECH_ENABLED", False))
        self.scope = (getattr(config, "SALUTE_SPEECH_SCOPE", None) or "SALUTE_SPEECH_PERS").strip()
        self.voice = (getattr(config, "SALUTE_SPEECH_VOICE", None) or "Nec_24000").strip()
        self.format = (getattr(config, "SALUTE_SPEECH_FORMAT", None) or "opus").strip() or "opus"
        self.atempo = resolve_prayer_tts_atempo()
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.auth_key)

    def _scope_candidates(self) -> list[str]:
        preferred = (self.scope or "SALUTE_SPEECH_PERS").strip() or "SALUTE_SPEECH_PERS"
        variants = [preferred]
        for alt in ("SALUTE_SPEECH_PERS", "SALUTE_SPEECH_CORP", "SBER_SPEECH"):
            if alt not in variants:
                variants.append(alt)
        return variants

    async def _request_token(self, session: aiohttp.ClientSession, *, scope: str) -> dict:
        headers = {
            "Authorization": f"Basic {self.auth_key}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        async with session.post(
            _OAUTH_URL,
            headers=headers,
            data={"scope": scope},
            ssl=False,  # Sber gateway часто требует корпоративный CA
        ) as resp:
            raw = await resp.text()
            try:
                data: Any = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"SaluteSpeech oauth HTTP {resp.status} non-json "
                    f"(scope={scope}): {raw[:300]!r}"
                ) from e
            if resp.status >= 400:
                raise RuntimeError(
                    f"SaluteSpeech oauth HTTP {resp.status} (scope={scope}): "
                    f"{str(data)[:400]}"
                )
            if not isinstance(data, dict):
                raise RuntimeError(
                    f"SaluteSpeech oauth: неожиданный ответ (scope={scope}): {data!r}"
                )
            return data

    @staticmethod
    def _is_scope_mismatch(exc: Exception) -> bool:
        return _SCOPE_MISMATCH in str(exc)

    async def _ensure_token(self, session: aiohttp.ClientSession) -> str:
        now = time.time()
        if self._token and now < self._token_expires_at - 60:
            return self._token
        last_error: Optional[Exception] = None
        data: Optional[dict] = None
        for scope in self._scope_candidates():
            try:
                data = await self._request_token(session, scope=scope)
                if scope != self.scope:
                    logger.warning(
                        "SaluteSpeech oauth: scope %s подошёл вместо %s",
                        scope,
                        self.scope,
                    )
                    self.scope = scope
                break
            except RuntimeError as e:
                last_error = e
                if not self._is_scope_mismatch(e):
                    # non-json / auth ошибки тоже пробуем следующий scope,
                    # но логируем тело — часто CORP возвращает HTML при PERS-ключе.
                    logger.warning("SaluteSpeech oauth failed for %s: %s", scope, e)
                    if "HTTP 401" in str(e) or "HTTP 403" in str(e):
                        raise
                    continue
                logger.warning("SaluteSpeech oauth scope mismatch for %s", scope)
        if data is None:
            if last_error:
                raise RuntimeError(
                    "SaluteSpeech oauth: ни один scope не подошёл к ключу. "
                    "Проверьте, что Authorization Key из проекта SaluteSpeech API "
                    f"(не GigaChat) и SALUTE_SPEECH_SCOPE совпадает с типом проекта. "
                    f"Последняя ошибка: {last_error}"
                ) from last_error
            raise RuntimeError("SaluteSpeech oauth: не удалось получить токен")
        token = (data.get("access_token") or "").strip()
        if not token:
            raise RuntimeError(f"SaluteSpeech oauth: нет access_token: {data!r}")
        # expires_at может быть ms timestamp или expires_in секунды
        exp = data.get("expires_at") or data.get("expires_in")
        if isinstance(exp, (int, float)) and exp > 10_000_000_000:
            self._token_expires_at = float(exp) / 1000.0
        elif isinstance(exp, (int, float)) and exp > 1_000_000_000:
            self._token_expires_at = float(exp)
        else:
            self._token_expires_at = now + float(exp or 1500)
        self._token = token
        return token

    async def synthesize_ogg_opus(self, text: str) -> bytes:
        if not self.enabled:
            raise RuntimeError("SaluteSpeech выключен (API закрыт)")
        if not self.configured:
            raise RuntimeError("SALUTE_SPEECH_AUTH_KEY не задан")

        body, mode = prepare_prayer_for_engine(text, engine="salute")
        if not body:
            raise ValueError("empty text")
        if len(body) > _MAX_CHARS:
            body = body[:_MAX_CHARS]

        params = {"format": self.format, "voice": self.voice}
        content_type = "application/ssml" if mode == "ssml" else "application/text"

        logger.info(
            "SaluteSpeech TTS start voice=%s format=%s chars=%s mode=%s scope=%s",
            self.voice,
            self.format,
            len(body),
            mode,
            self.scope,
        )

        timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SEC)

        async def _request() -> bytes:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                token = await self._ensure_token(session)
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": content_type,
                }
                async with session.post(
                    _SYNTH_URL,
                    params=params,
                    headers=headers,
                    data=body.encode("utf-8"),
                    ssl=False,
                ) as resp:
                    raw = await resp.read()
                    if resp.status >= 400:
                        err = raw.decode("utf-8", errors="replace")[:500]
                        raise RuntimeError(f"SaluteSpeech synth HTTP {resp.status}: {err}")
                    if not raw:
                        raise RuntimeError("SaluteSpeech вернул пустой ответ")
                    return raw

        audio = await asyncio.wait_for(_request(), timeout=_TIMEOUT_SEC)
        # format=opus уже ogg/opus; при SSML rate не дублируем atempo.
        post_atempo = 1.0 if mode == "ssml" else self.atempo
        ogg = await asyncio.to_thread(
            audio_bytes_to_ogg_opus, audio, atempo=post_atempo, prefix="salute_"
        )
        if not ogg:
            raise RuntimeError("ffmpeg не смог обработать SaluteSpeech audio")
        logger.info(
            "SaluteSpeech TTS ok voice=%s bytes=%s post_atempo=%.3f",
            self.voice,
            len(ogg),
            post_atempo,
        )
        return ogg
