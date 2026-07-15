"""
Клиент для работы с DeepSeek API с историей и знаниями о клубе.
"""

import json
import logging
import os
import asyncio
import re
import uuid
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from openai import AsyncOpenAI  # ← AsyncOpenAI, не OpenAI!

from storage.db.llm_token_normalize import extract_token_counts_and_extras
from bot.utils.telegram_html import filter_concrete_quick_reply_choices
from bot.followup_segments import sensitive_context_system_addon
from bot.texts.prompts.agents_club_manager import build_club_manager_system_prompt
from bot.texts.prompts.quick_reply_extractor import (
    QUICK_REPLY_EXTRACTOR_SYSTEM,
    QUICK_REPLY_USER_PREFIX,
)
from bot.texts.prompts.followup_segments import SENSITIVE_AGENT_ADDON
from openai_client.club_rag_prompt import augment_system_prompt_with_rag
from config import config, rag_retrieval_settings_from_config
from openai_client.rag_search_planner import (
    build_history_tail,
    retrieve_for_user_message,
)
from bot.services.agent_datetime_context import prepend_datetime_context

if TYPE_CHECKING:
    from rag.runtime import RagStack

logger = logging.getLogger(__name__)


class DeepSeekTimeoutError(Exception):
    """Таймаут DeepSeek chat completion."""


class AgentsClient:
    CHAT_MODEL = "deepseek-chat"

    def __init__(
        self,
        user_storage,
        *,
        system_prompt_override: Optional[str] = None,
        rag_stack: Optional["RagStack"] = None,
    ):
        self.user_storage = user_storage
        self.rag_stack = rag_stack
        self._use_static_system_prompt = system_prompt_override is not None

        # Клиент DeepSeek — асинхронный
        self.client = AsyncOpenAI(  # ← AsyncOpenAI
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com/v1",
            timeout=30.0,
            max_retries=2
        )

        if system_prompt_override is not None:
            self.club_knowledge = ""
            self.system_prompt = system_prompt_override
            logger.info(
                "✅ AgentsClient: кастомный system prompt (%s символов)",
                len(system_prompt_override),
            )
        else:
            self.club_knowledge = self._load_club_knowledge()
            self.system_prompt = self._build_system_prompt()
            if rag_stack is not None:
                ne, ng = rag_stack.expert_count_golden_count()
                logger.info(
                    "✅ AgentsClient: внешний prod-RAG read-only (expert≈%s, golden≈%s)",
                    ne,
                    ng,
                )
    
    def _load_club_knowledge(self) -> str:
        """Загружает содержимое файла с описанием клуба."""
        repo_root = Path(__file__).resolve().parent.parent
        candidates = (
            repo_root / "bot" / "texts" / "aboutclub.txt",
            repo_root / "aboutclub.txt",
        )
        try:
            for file_path in candidates:
                if file_path.exists():
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    logger.info("✅ Загружен файл клуба %s: %s символов", file_path, len(content))
                    return content
            raise FileNotFoundError("aboutclub.txt not found")
        except Exception as e:
            logger.error(f"❌ Не удалось загрузить файл клуба: {e}")
            return ""
    
    def _build_system_prompt(self) -> str:
        """Собирает системный промт с знаниями о клубе."""
        return build_club_manager_system_prompt(self.club_knowledge)

    def _attach_datetime_context(self, system_content: str) -> str:
        return prepend_datetime_context(system_content)

    # Сколько последних сообщений из DM подаём агенту в контекст.
    HISTORY_LIMIT = 20

    @staticmethod
    def _history_role_counts(history: List[Dict[str, str]]) -> Dict[str, int]:
        counts: Dict[str, int] = {"user": 0, "assistant": 0, "other": 0}
        for msg in history:
            role = (msg.get("role") or "").strip().lower()
            if role in ("user", "assistant"):
                counts[role] += 1
            else:
                counts["other"] += 1
        return counts

    @staticmethod
    def _no_greeting_context_addon(history: List[Dict[str, str]]) -> str:
        """Если в БД только реплики пользователя — модель «не видит» прошлых ответов."""
        counts = AgentsClient._history_role_counts(history)
        user_turns = counts["user"]
        if user_turns < 2:
            return ""
        if counts["assistant"] > 0:
            return ""
        return (
            "\n\n🔴 В истории несколько сообщений пользователя подряд без твоих ответов. "
            "Человек уже в диалоге — не начинай с приветствия («Привет», «Здравствуйте» и т.п.), "
            "отвечай сразу по сути последнего сообщения."
        )

    def _log_agent_request_dump(
        self,
        *,
        user_id: int,
        user_message: str,
        history: List[Dict[str, str]],
        messages: List[Dict[str, str]],
        sensitive: bool,
        retrieved: str = "",
        golden_block: str = "",
        rag_plan: Any = None,
        history_user_message_appended: bool = False,
    ) -> None:
        """Одна JSON-строка в лог: весь payload, уходящий в chat.completions."""
        if not config.LLM_AGENT_REQUEST_DUMP:
            return

        system_content = messages[0]["content"] if messages else ""
        rag_section: Dict[str, Any] = {
            "config_rag_enabled": config.RAG_ENABLED,
            "rag_stack_attached": self.rag_stack is not None,
            "expert_context": retrieved or None,
            "golden_few_shot": golden_block or None,
            "expert_chars": len(retrieved or ""),
            "golden_chars": len(golden_block or ""),
        }
        if rag_plan is not None and is_dataclass(rag_plan):
            rag_section["search_plan"] = asdict(rag_plan)

        role_counts = self._history_role_counts(history)

        payload: Dict[str, Any] = {
            "event": "llm_agent_request_dump",
            "user_id": user_id,
            "model": self.CHAT_MODEL,
            "flags": {
                "sensitive_context": sensitive,
                "static_system_prompt_override": self._use_static_system_prompt,
                "history_user_message_appended": history_user_message_appended,
            },
            "counts": {
                "history_db_rows": len(history),
                "history_roles": role_counts,
                "messages_to_llm": len(messages),
                "system_prompt_chars": len(system_content),
            },
            "rag": rag_section,
            "history_from_db": history,
            "current_user_message": user_message,
            "messages_to_llm": messages,
        }
        logger.info(
            "LLM_AGENT_REQUEST_DUMP %s",
            json.dumps(payload, ensure_ascii=False, default=str),
        )

    async def run(self, user_message: str, user_id: int) -> Optional[str]:
        """Отправляет запрос к DeepSeek с учётом истории и возвращает ответ.

        Контекст берётся из единого лога messages (chat_type='private') —
        туда попадают и реплики юзера, и ответы агента, и системные исходящие
        бота (рассылки, ответы саппорта, онбординг и т. п.). Записывать
        ничего не нужно: входящее уже сохранено InboundLoggingMiddleware,
        ответ будет сохранён OutgoingLoggingMiddleware.
        """
        try:
            history = await self.user_storage.get_private_chat_history(
                user_id, limit=self.HISTORY_LIMIT
            )

            sensitive = sensitive_context_system_addon(user_message, history)
            retrieved, golden_block = "", ""
            rag_plan = None

            if self._use_static_system_prompt:
                system_content = self.system_prompt
            elif sensitive:
                system_content = self.system_prompt + SENSITIVE_AGENT_ADDON
                logger.info(
                    "🔴 Sensitive context for user %s — no sales/support/RAG",
                    user_id,
                )
            else:
                if self.rag_stack is not None:
                    history_tail = (
                        build_history_tail(history, max_messages=6)
                        if history
                        else None
                    )
                    rag_settings = rag_retrieval_settings_from_config(config)
                    retrieved, golden_block, _, rag_plan = await retrieve_for_user_message(
                        self.rag_stack,
                        user_message,
                        llm_client=self.client,
                        llm_model=self.CHAT_MODEL,
                        history_tail=history_tail,
                        settings=rag_settings,
                        user_id=user_id,
                        user_storage=self.user_storage,
                    )
                    if retrieved or golden_block:
                        logger.info(
                            "📚 RAG user %s plan=%s expert=%s golden=%s",
                            user_id,
                            rag_plan.target_dates_iso or rag_plan.semantic_queries[:2],
                            len(retrieved or ""),
                            len(golden_block or ""),
                        )
                base = self.system_prompt
                system_content = augment_system_prompt_with_rag(
                    base,
                    retrieved_context=retrieved or "",
                    golden_block=golden_block or "",
                )

            system_content = self._attach_datetime_context(system_content)
            system_content += self._no_greeting_context_addon(history)

            if not sensitive and not self._use_static_system_prompt:
                from bot.services.promo_campaign_service import build_promo_agent_addon

                promo_addon = await build_promo_agent_addon(self.user_storage, user_id)
                if promo_addon:
                    system_content += "\n\n" + promo_addon

            messages = [{"role": "system", "content": system_content}]
            for msg in history:
                messages.append({"role": msg["role"], "content": msg["content"]})

            # Страховка: если по какой-то причине текущее входящее ещё не доехало
            # в БД (например, middleware не отработал), добавляем его явно.
            history_user_message_appended = False
            if not history or history[-1]["role"] != "user" or history[-1]["content"] != user_message:
                messages.append({"role": "user", "content": user_message})
                history_user_message_appended = True

            self._log_agent_request_dump(
                user_id=user_id,
                user_message=user_message,
                history=history,
                messages=messages,
                sensitive=bool(sensitive),
                retrieved=retrieved,
                golden_block=golden_block,
                rag_plan=rag_plan,
                history_user_message_appended=history_user_message_appended,
            )

            logger.info(
                "📨 DeepSeek request for user %s: %s messages (history=%s)",
                user_id, len(messages), len(history),
            )

            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.CHAT_MODEL,
                    messages=messages,
                    temperature=0.7,
                    max_tokens=2048,
                ),
                timeout=25.0,
            )

            usage = getattr(response, "usage", None)
            request_id = str(uuid.uuid4())
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

            return response.choices[0].message.content

        except asyncio.TimeoutError as e:
            logger.error(f"❌ DeepSeek timeout for user {user_id}")
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type="deepseek_timeout",
                data={"model": self.CHAT_MODEL},
                source="deepseek",
                outcome="error",
            )
            raise DeepSeekTimeoutError(
                f"DeepSeek timeout for user {user_id}"
            ) from e
        except Exception as e:
            logger.error(f"❌ DeepSeek API error for user {user_id}: {e}")
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type="deepseek_api_error",
                data={"model": self.CHAT_MODEL, "error": str(e)},
                source="deepseek",
                outcome="error",
            )
            return None

    async def extract_quick_reply_choices(
        self,
        assistant_plain_text: str,
        *,
        user_id: int,
    ) -> List[str]:
        """Второй короткий вызов: извлечь 2–4 короткие подписи для inline-кнопок или []."""
        if not assistant_plain_text or "?" not in assistant_plain_text:
            return []

        trimmed = assistant_plain_text.strip()
        if len(trimmed) < 20:
            return []

        try:
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.CHAT_MODEL,
                    messages=[
                        {"role": "system", "content": prepend_datetime_context(QUICK_REPLY_EXTRACTOR_SYSTEM)},
                        {
                            "role": "user",
                            "content": f"{QUICK_REPLY_USER_PREFIX}\n\n{trimmed[:6000]}",
                        },
                    ],
                    temperature=0.2,
                    max_tokens=256,
                ),
                timeout=12.0,
            )
            raw = (response.choices[0].message.content or "").strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
                raw = re.sub(r"\s*```\s*$", "", raw)

            data = json.loads(raw)
            choices = data.get("choices")
            if not isinstance(choices, list):
                return []

            out: List[str] = []
            for c in choices:
                s = str(c).strip()
                if not s or len(s) > 64:
                    continue
                if s not in out:
                    out.append(s)
                if len(out) >= 4:
                    break

            out = filter_concrete_quick_reply_choices(out)

            usage = getattr(response, "usage", None)
            request_id = str(uuid.uuid4())
            await self.user_storage.log_llm_completion_usage(
                user_id=user_id,
                provider="deepseek",
                model=self.CHAT_MODEL,
                usage=usage,
                request_kind="quick_reply_extract",
                request_id=request_id,
            )
            pt, ct, tt, *_ = extract_token_counts_and_extras(usage)
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type=f"deepseek_{self.CHAT_MODEL}_quick_reply_extract",
                data={
                    "provider": "deepseek",
                    "request_id": request_id,
                    "model": self.CHAT_MODEL,
                    "prompt_tokens": pt,
                    "completion_tokens": ct,
                    "total_tokens": tt,
                    "choices_n": len(out),
                },
                source="deepseek",
                outcome="success",
            )
            return out

        except asyncio.TimeoutError:
            logger.warning("quick_reply extract timeout user=%s", user_id)
            return []
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning("quick_reply extract parse/error user=%s: %s", user_id, e)
            return []
        except Exception as e:
            logger.error("quick_reply extract failed user=%s: %s", user_id, e)
            return []