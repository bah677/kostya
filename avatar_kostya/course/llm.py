"""Вызов LLM по имени модели: DeepSeek или OpenAI, JSON-режим, token_usage."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional, Sequence

from openai import AsyncOpenAI

from storage.db.llm_token_normalize import extract_token_counts_and_extras

logger = logging.getLogger(__name__)

_DEEPSEEK_PREFIXES = ("deepseek",)


def is_deepseek_model(model: str) -> bool:
    m = (model or "").strip().lower()
    return any(m.startswith(p) for p in _DEEPSEEK_PREFIXES)


def _uses_max_completion_tokens(model: str) -> bool:
    """Новые OpenAI-модели отвергают max_tokens в пользу max_completion_tokens."""
    if is_deepseek_model(model):
        return False
    mid = (model or "").strip().lower()
    if not mid:
        return False
    if mid.startswith(("o1", "o3", "o4", "gpt-5", "gpt-6", "chatgpt-")):
        return True
    # Cursor / Sol и прочие семейства вида *-sol*
    if "-sol" in mid or mid.endswith("sol"):
        return True
    return False


def _token_limit_kwargs(model: str, max_tokens: int) -> Dict[str, Any]:
    n = max(200, min(16000, int(max_tokens)))
    if _uses_max_completion_tokens(model):
        return {"max_completion_tokens": n}
    return {"max_tokens": n}


def _parse_json_obj(raw: str) -> dict:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}


class CourseLLM:
    def __init__(self, user_storage):
        self.user_storage = user_storage
        self._openai = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self._deepseek = AsyncOpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com/v1",
            timeout=150.0,
            max_retries=2,
        )

    def _client(self, model: str) -> AsyncOpenAI:
        return self._deepseek if is_deepseek_model(model) else self._openai

    def _provider(self, model: str) -> str:
        return "deepseek" if is_deepseek_model(model) else "openai"

    async def complete(
        self,
        *,
        model: str,
        messages: Sequence[Dict[str, str]],
        user_id: int = 0,
        temperature: float = 0.4,
        max_tokens: int = 2500,
        json_mode: bool = False,
        request_kind: str = "course_llm",
    ) -> str:
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "temperature": temperature,
            **_token_limit_kwargs(model, max_tokens),
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        client = self._client(model)
        try:
            resp = await client.chat.completions.create(**kwargs)
        except Exception as e:
            err = str(e).lower()
            # gpt-5/6/o* и т.п.: API просит max_completion_tokens вместо max_tokens
            if (
                "max_tokens" in kwargs
                and "max_completion_tokens" in err
                and ("unsupported" in err or "not supported" in err)
            ):
                n = int(kwargs.pop("max_tokens"))
                kwargs["max_completion_tokens"] = n
                try:
                    resp = await client.chat.completions.create(**kwargs)
                except Exception as e2:
                    logger.error("CourseLLM failed model=%s: %s", model, e2)
                    raise
            elif json_mode and "response_format" in err:
                kwargs.pop("response_format", None)
                resp = await client.chat.completions.create(**kwargs)
            else:
                logger.error("CourseLLM failed model=%s: %s", model, e)
                raise
        text = ""
        if resp.choices and resp.choices[0].message:
            text = (resp.choices[0].message.content or "").strip()
        usage = getattr(resp, "usage", None)
        await self._log(user_id, model, usage, request_kind)
        return text

    async def complete_json(self, **kwargs) -> dict:
        raw = await self.complete(json_mode=True, **kwargs)
        return _parse_json_obj(raw)

    async def _log(self, user_id: int, model: str, usage: Any, request_kind: str) -> None:
        if not self.user_storage:
            return
        try:
            provider = self._provider(model)
            request_id = str(uuid.uuid4())
            await self.user_storage.log_llm_completion_usage(
                user_id=int(user_id or 0),
                provider=provider,
                model=model,
                usage=usage,
                request_kind=request_kind,
                request_id=request_id,
            )
        except Exception as e:
            logger.warning("CourseLLM log: %s", e)


parse_json_obj = _parse_json_obj
