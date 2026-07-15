"""
Клиент DeepSeek API с историей диалога из БД (messages).

Системный промт по умолчанию — из ``scripture_prompt`` (проект «Библия»).
Опционально можно передать другой промт (тесты, другая сборка).
"""

import asyncio
import html as html_module
import logging
import os
import re
import uuid
from typing import Dict, List, Optional

from openai import AsyncOpenAI

from openai_client.scripture_prompt import BIBLIA_AGENT_SYSTEM_PROMPT
from bot.utils.telegram_html import strip_llm_code_fence
from storage.db.llm_token_normalize import extract_token_counts_and_extras

logger = logging.getLogger(__name__)

# Таймауты DeepSeek: HTTP-клиент не должен быть короче asyncio.wait_for на запрос.
_DEEPSEEK_HTTP_TIMEOUT_SEC = 150.0
_DEEPSEEK_CHAT_WAIT_SEC = 120.0
_DEEPSEEK_HTML_FORMAT_WAIT_SEC = 120.0

# token_usage / аналитика: дополнительный вызов только для разметки → HTML (DeepSeek).
TELEGRAM_HTML_FORMAT_REQUEST_KIND = "telegram_html_format_auxiliary"

_TELEGRAM_HTML_FORMAT_SYSTEM = (
    "Ты приводишь текст к разметке Telegram Bot API (parse_mode HTML).\n"
    "На входе — ответ библейского ассистента, иногда с Markdown (** ## ``` и т.п.), "
    "да и краткий контекст диалога (если дан — для согласованности, не дублируй его в выводе).\n"
    "На выходе верни ОДИН блок текста: только теги "
    "<b>, <strong>, <i>, <em>, <u>, <s>, <code>, <pre>, "
    "<a href=\"https://…\">, <blockquote>. "
    "Стихи и цитаты Писания: <blockquote>текст\\n\\n<i>(книга глава:стих)</i></blockquote> "
    "(источник только внутри blockquote, курсивом в скобках).\n"
    "Не используй Markdown-символы. Не добавляй пояснений, только готовый HTML-текст."
)


def _strip_for_history_match(s: str) -> str:
    """Сравнение текста из БД (может быть HTML) с текстом из aiogram.message.text."""
    if not s:
        return ""
    t = re.sub(r"<[^>]+>", " ", s)
    t = html_module.unescape(t)
    return " ".join(t.split())


def _format_context_tail(history: List[dict], *, max_turns: int = 8, max_chars_per: int = 1200) -> str:
    lines = []
    for msg in history[-max_turns:]:
        role = msg.get("role") or ""
        content = (msg.get("content") or "")[:max_chars_per]
        if not content.strip():
            continue
        label = "Пользователь" if role == "user" else "Ассистент"
        lines.append(f"{label}: {content}")
    return "\n".join(lines)


class AgentsClient:
    CHAT_MODEL = "deepseek-chat"

    def __init__(self, user_storage, *, system_prompt_override: Optional[str] = None):
        self.user_storage = user_storage

        self.client = AsyncOpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com/v1",
            timeout=_DEEPSEEK_HTTP_TIMEOUT_SEC,
            max_retries=2,
        )

        self.system_prompt = (
            system_prompt_override
            if system_prompt_override is not None
            else BIBLIA_AGENT_SYSTEM_PROMPT
        )
        src = "override" if system_prompt_override is not None else "scripture_prompt"
        logger.info(
            "✅ AgentsClient: system prompt из %s (%s символов)",
            src,
            len(self.system_prompt),
        )

    # Сколько последних сообщений из DM подаём агенту в контекст.
    HISTORY_LIMIT = 20

    async def run(self, user_message: str, user_id: int) -> Optional[str]:
        """Запрос к DeepSeek с историей; ответ логируется middleware'ами."""
        try:
            history = await self.user_storage.get_private_chat_history(
                user_id, limit=self.HISTORY_LIMIT
            )

            messages = [{"role": "system", "content": self.system_prompt}]
            for msg in history:
                messages.append({"role": msg["role"], "content": msg["content"]})

            if (
                not history
                or history[-1]["role"] != "user"
                or _strip_for_history_match(history[-1]["content"])
                != _strip_for_history_match(user_message)
            ):
                messages.append({"role": "user", "content": user_message})

            logger.info(
                "📨 DeepSeek request for user %s: %s messages (history_rows=%s, dup_user_skipped=%s)",
                user_id,
                len(messages),
                len(history),
                bool(
                    history
                    and history[-1]["role"] == "user"
                    and _strip_for_history_match(history[-1]["content"])
                    == _strip_for_history_match(user_message)
                ),
            )

            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.CHAT_MODEL,
                    messages=messages,
                    temperature=0.7,
                    max_tokens=2048,
                ),
                timeout=_DEEPSEEK_CHAT_WAIT_SEC,
            )

            usage = getattr(response, "usage", None)
            request_id = str(uuid.uuid4())
            reply_text: Optional[str] = None
            if response.choices and response.choices[0].message:
                reply_text = response.choices[0].message.content

            await self.user_storage.log_llm_completion_usage(
                user_id=user_id,
                provider="deepseek",
                model=self.CHAT_MODEL,
                usage=usage,
                request_kind="chat_completion",
                request_id=request_id,
            )
            pt, ct, tt, *_ = extract_token_counts_and_extras(usage)
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type=f"deepseek_{self.CHAT_MODEL}_chat_completion",
                data={
                    "provider": "deepseek",
                    "request_id": request_id,
                    "model": self.CHAT_MODEL,
                    "prompt_tokens": pt,
                    "completion_tokens": ct,
                    "total_tokens": tt,
                },
                source="deepseek",
                outcome="success",
            )

            return reply_text

        except asyncio.TimeoutError:
            logger.error("❌ DeepSeek timeout for user %s", user_id)
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type="deepseek_timeout",
                data={"model": self.CHAT_MODEL},
                source="deepseek",
                outcome="error",
            )
            return None
        except Exception as e:
            logger.error("❌ DeepSeek API error for user %s: %s", user_id, e)
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type="deepseek_api_error",
                data={"model": self.CHAT_MODEL, "error": str(e)},
                source="deepseek",
                outcome="error",
            )
            return None

    async def format_reply_to_telegram_html(self, raw_text: str, user_id: int) -> Optional[str]:
        """
        Отдельный вызов DeepSeek: Markdown/plain → HTML Telegram.
        В контекст подмешиваются последние реплики из того же источника, что и основной диалог.
        ``request_kind=telegram_html_format_auxiliary`` в token_usage.
        """
        try:
            hist = await self.user_storage.get_private_chat_history(
                user_id, limit=self.HISTORY_LIMIT
            )
            ctx = _format_context_tail(hist)
            if ctx.strip():
                user_block = (
                    "Контекст диалога (для согласованности; не копируй его в ответ, только разметь текст ниже):\n"
                    f"{ctx}\n\n---\n\n"
                    "Отформатируй в Telegram HTML только следующий ответ ассистента:\n\n"
                    f"{raw_text}"
                )
            else:
                user_block = (
                    "Отформатируй в Telegram HTML следующий текст (ответ ассистента):\n\n"
                    f"{raw_text}"
                )

            messages = [
                {"role": "system", "content": _TELEGRAM_HTML_FORMAT_SYSTEM},
                {"role": "user", "content": user_block},
            ]
            est_out = min(8192, max(512, len(user_block) // 3 + len(raw_text) // 2 + 1024))

            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.CHAT_MODEL,
                    messages=messages,
                    temperature=0.15,
                    max_tokens=est_out,
                ),
                timeout=_DEEPSEEK_HTML_FORMAT_WAIT_SEC,
            )

            usage = getattr(response, "usage", None)
            request_id = str(uuid.uuid4())
            out = (
                response.choices[0].message.content.strip()
                if response.choices
                and response.choices[0].message
                and response.choices[0].message.content
                else ""
            )
            out = strip_llm_code_fence(out)

            await self.user_storage.log_llm_completion_usage(
                user_id=user_id,
                provider="deepseek",
                model=self.CHAT_MODEL,
                usage=usage,
                request_kind=TELEGRAM_HTML_FORMAT_REQUEST_KIND,
                request_id=request_id,
                metadata={
                    "purpose": "telegram_html_auxiliary_format",
                    "is_auxiliary_llm_formatting": True,
                    "formatter": "deepseek",
                    "input_chars": len(raw_text),
                    "output_chars": len(out),
                },
            )
            pt, ct, tt, *_ = extract_token_counts_and_extras(usage)
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type=f"deepseek_{TELEGRAM_HTML_FORMAT_REQUEST_KIND}",
                data={
                    "provider": "deepseek",
                    "request_id": request_id,
                    "model": self.CHAT_MODEL,
                    "request_kind": TELEGRAM_HTML_FORMAT_REQUEST_KIND,
                    "prompt_tokens": pt,
                    "completion_tokens": ct,
                    "total_tokens": tt,
                },
                source="deepseek",
                outcome="success",
            )
            return out or None

        except asyncio.TimeoutError:
            logger.error("❌ DeepSeek HTML format timeout for user %s", user_id)
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type="deepseek_telegram_html_format_timeout",
                data={"model": self.CHAT_MODEL},
                source="deepseek",
                outcome="error",
            )
            return None
        except Exception as e:
            logger.error("❌ DeepSeek HTML format error for user %s: %s", user_id, e)
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type="deepseek_telegram_html_format_error",
                data={"model": self.CHAT_MODEL, "error": str(e)},
                source="deepseek",
                outcome="error",
            )
            return None

    async def complete(
        self,
        *,
        system_prompt: str,
        user_content: str,
        user_id: int,
        request_kind: str = "chat_completion",
        temperature: float = 0.6,
        max_tokens: int = 2048,
    ) -> Optional[str]:
        """Один изолированный запрос к DeepSeek без истории из БД."""
        try:
            logger.info(
                "DeepSeek complete start kind=%s user=%s max_tokens=%s",
                request_kind,
                user_id,
                max_tokens,
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.CHAT_MODEL,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ),
                timeout=_DEEPSEEK_CHAT_WAIT_SEC,
            )
            usage = getattr(response, "usage", None)
            request_id = str(uuid.uuid4())
            reply_text: Optional[str] = None
            if response.choices and response.choices[0].message:
                reply_text = response.choices[0].message.content

            await self.user_storage.log_llm_completion_usage(
                user_id=user_id,
                provider="deepseek",
                model=self.CHAT_MODEL,
                usage=usage,
                request_kind=request_kind,
                request_id=request_id,
            )
            logger.info(
                "DeepSeek complete done kind=%s user=%s reply_chars=%s",
                request_kind,
                user_id,
                len(reply_text or ""),
            )
            return (reply_text or "").strip() or None
        except Exception as e:
            logger.error("❌ DeepSeek complete (%s) user=%s: %s", request_kind, user_id, e)
            return None

    async def complete_with_messages(
        self,
        *,
        messages: List[Dict[str, str]],
        user_id: int,
        request_kind: str = "chat_completion",
        temperature: float = 0.6,
        max_tokens: int = 2048,
    ) -> Optional[str]:
        """Запрос с полноценной историей сообщений (system/user/assistant)."""
        try:
            logger.info(
                "DeepSeek complete_with_messages kind=%s user=%s turns=%s",
                request_kind,
                user_id,
                len(messages),
            )
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.CHAT_MODEL,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ),
                timeout=_DEEPSEEK_CHAT_WAIT_SEC,
            )
            usage = getattr(response, "usage", None)
            request_id = str(uuid.uuid4())
            reply_text: Optional[str] = None
            if response.choices and response.choices[0].message:
                reply_text = response.choices[0].message.content

            await self.user_storage.log_llm_completion_usage(
                user_id=user_id,
                provider="deepseek",
                model=self.CHAT_MODEL,
                usage=usage,
                request_kind=request_kind,
                request_id=request_id,
            )
            return (reply_text or "").strip() or None
        except Exception as e:
            logger.error(
                "❌ DeepSeek complete_with_messages (%s) user=%s: %s",
                request_kind,
                user_id,
                e,
            )
            return None
