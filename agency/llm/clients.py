"""LLM clients: DeepSeek / OpenAI / Anthropic."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx
from openai import AsyncOpenAI

from config import Config

logger = logging.getLogger(__name__)


@dataclass
class LlmResult:
    text: str
    provider: str
    model: str
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    ok: bool = True
    error: str = ""
    has_web: bool = False


class LlmHub:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._openai = (
            AsyncOpenAI(api_key=cfg.OPENAI_API_KEY) if cfg.OPENAI_API_KEY else None
        )
        self._deepseek = (
            AsyncOpenAI(
                api_key=cfg.DEEPSEEK_API_KEY,
                base_url="https://api.deepseek.com",
            )
            if cfg.DEEPSEEK_API_KEY
            else None
        )
        self._anthropic = None
        if cfg.ANTHROPIC_API_KEY:
            try:
                import anthropic

                self._anthropic = anthropic.AsyncAnthropic(api_key=cfg.ANTHROPIC_API_KEY)
            except Exception as e:
                logger.warning("anthropic init failed: %s", e)

    @property
    def has_openai(self) -> bool:
        return self._openai is not None

    @property
    def has_deepseek(self) -> bool:
        return self._deepseek is not None

    @property
    def has_anthropic(self) -> bool:
        return self._anthropic is not None

    @property
    def has_web(self) -> bool:
        return bool(self.cfg.OPENAI_API_KEY)

    async def chat(
        self,
        *,
        provider: str,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ) -> LlmResult:
        t0 = time.monotonic()
        try:
            if provider == "deepseek":
                return await self._openai_compat(
                    self._deepseek, "deepseek", model, system, user, temperature, max_tokens, t0
                )
            if provider == "openai":
                return await self._openai_compat(
                    self._openai, "openai", model, system, user, temperature, max_tokens, t0
                )
            if provider == "anthropic":
                return await self._claude(model, system, user, temperature, max_tokens, t0)
            return LlmResult(
                text="", provider=provider, model=model, ok=False, error="unknown provider"
            )
        except Exception as e:
            logger.exception("llm %s/%s failed", provider, model)
            return LlmResult(
                text="",
                provider=provider,
                model=model,
                ok=False,
                error=str(e)[:500],
                latency_ms=int((time.monotonic() - t0) * 1000),
            )

    async def _openai_compat(
        self, client, provider, model, system, user, temperature, max_tokens, t0
    ) -> LlmResult:
        if client is None:
            return LlmResult(
                text="", provider=provider, model=model, ok=False, error="client not configured"
            )
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = (resp.choices[0].message.content or "").strip()
        usage = getattr(resp, "usage", None)
        return LlmResult(
            text=text,
            provider=provider,
            model=model,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            latency_ms=int((time.monotonic() - t0) * 1000),
            ok=True,
        )

    async def _claude(self, model, system, user, temperature, max_tokens, t0) -> LlmResult:
        if self._anthropic is None:
            return LlmResult(
                text="", provider="anthropic", model=model, ok=False, error="no anthropic"
            )
        resp = await self._anthropic.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        parts = []
        for block in resp.content:
            if getattr(block, "type", "") == "text":
                parts.append(block.text)
        usage = getattr(resp, "usage", None)
        return LlmResult(
            text="\n".join(parts).strip(),
            provider="anthropic",
            model=model,
            prompt_tokens=getattr(usage, "input_tokens", None),
            completion_tokens=getattr(usage, "output_tokens", None),
            latency_ms=int((time.monotonic() - t0) * 1000),
            ok=True,
        )

    async def web_research(self, query: str) -> LlmResult:
        """OpenAI search-preview model (web)."""
        t0 = time.monotonic()
        if not self._openai:
            return LlmResult(
                text="",
                provider="openai",
                model=self.cfg.OPENAI_WEB_MODEL,
                ok=False,
                error="OPENAI_API_KEY missing — web research skipped",
                has_web=True,
            )
        model = self.cfg.OPENAI_WEB_MODEL
        try:
            # search-preview models often reject custom temperature
            resp = await self._openai.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ты researcher для христианского bible-бота и digital-ministry. "
                            "Ищи актуальные best practices retention/donations/funnels в ботах "
                            "и вере-контенте. Дай 3–6 тезисов со ссылками если есть. "
                            "Русский язык. Без воды."
                        ),
                    },
                    {"role": "user", "content": query},
                ],
            )
            text = (resp.choices[0].message.content or "").strip()
            usage = getattr(resp, "usage", None)
            return LlmResult(
                text=text,
                provider="openai",
                model=model,
                prompt_tokens=getattr(usage, "prompt_tokens", None),
                completion_tokens=getattr(usage, "completion_tokens", None),
                latency_ms=int((time.monotonic() - t0) * 1000),
                ok=True,
                has_web=True,
            )
        except Exception as e:
            # fallback: non-web model with honesty
            logger.warning("web model failed (%s), fallback chat", e)
            fb = await self.chat(
                provider="openai",
                model=self.cfg.OPENAI_MODEL,
                system=(
                    "Web search недоступен. Честно скажи, что внешних источников нет, "
                    "и дай 2–3 общих принципа без выдуманных ссылок и статистики."
                ),
                user=query,
                temperature=0.2,
                max_tokens=800,
            )
            fb.has_web = False
            fb.error = f"web_failed:{e}"
            return fb
