# bot/features/messaging.py
import asyncio
import logging
import time
from collections import Counter, deque
from typing import Deque, Optional, Tuple

from aiogram import Dispatcher
from aiogram.enums import ChatType, ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from bot.features.base import BaseFeature
from bot.features.background_jobs import (
    background_jobs_paused,
    set_background_jobs_paused,
)
from bot.utils.admin_channel import send_admin_html_message
from bot.utils.telegram_html import strip_subscribe_cta
from bot.utils.telegram_html_async import normalize_llm_reply_for_telegram_async
from config import config

logger = logging.getLogger(__name__)

_RETRY_DELAYS = (2.0, 6.0, 15.0)
# 1–2: DeepSeek как есть; 3: без thinking; 4: OpenAI
_RETRY_STEPS = (
    {"thinking": None, "prefer_openai": False},
    {"thinking": None, "prefer_openai": False},
    {"thinking": "disabled", "prefer_openai": False},
    {"thinking": None, "prefer_openai": True},
)
_SOFT_FAIL_TEXT = (
    "Мне нужно немного больше времени, чтобы ответить.\n"
    "Я вернусь к вашему сообщению — не нужно писать заново."
)

# sliding window: (monotonic_ts, ok:bool)
_GEN_ATTEMPTS: Deque[Tuple[float, bool]] = deque(maxlen=500)
# recent fail reasons in the same window: (monotonic_ts, reason)
_GEN_FAIL_REASONS: Deque[Tuple[float, str]] = deque(maxlen=200)


def _record_gen_attempt(ok: bool, *, reason: str = "") -> None:
    now = time.monotonic()
    _GEN_ATTEMPTS.append((now, bool(ok)))
    if not ok and reason:
        _GEN_FAIL_REASONS.append((now, reason.strip()[:160]))


def _gen_error_rate_5m() -> Tuple[float, int, int]:
    now = time.monotonic()
    window = [x for x in _GEN_ATTEMPTS if now - x[0] <= 300.0]
    if not window:
        return 0.0, 0, 0
    fails = sum(1 for _, ok in window if not ok)
    total = len(window)
    return fails / total, fails, total


def _short_fail_reason(err: Optional[BaseException], *, finish: Optional[str] = None) -> str:
    if err is None and not finish:
        return "неизвестно"
    msg = (str(err) if err else "").strip()
    low = msg.lower()
    fin = (finish or "").strip().lower()
    if "empty_response" in low or not msg and fin:
        if fin == "length" or "finish=length" in low:
            return "DeepSeek: пустой ответ (finish=length — лимит токенов/reasoning)"
        if fin:
            return f"DeepSeek: пустой ответ (finish={finish})"
        return "DeepSeek: пустой ответ"
    if "timeout" in low or isinstance(err, asyncio.TimeoutError):
        return "таймаут DeepSeek/OpenAI"
    if "429" in low or "rate limit" in low:
        return "лимит запросов API (429)"
    if "402" in low or "insufficient" in low or "balance" in low:
        return "баланс/оплата API"
    if "503" in low or "502" in low or "500" in low or "overloaded" in low:
        return "API временно недоступен (5xx)"
    if "connect" in low or "connection" in low:
        return "сеть/соединение с API"
    name = type(err).__name__ if err else "Error"
    brief = msg.replace("\n", " ")[:100] if msg else name
    return f"{name}: {brief}" if msg else name


def _top_fail_reasons_5m(*, limit: int = 3) -> str:
    now = time.monotonic()
    reasons = [r for ts, r in _GEN_FAIL_REASONS if now - ts <= 300.0]
    if not reasons:
        return ""
    counts = Counter(reasons).most_common(limit)
    parts = [f"{reason} ×{n}" if n > 1 else reason for reason, n in counts]
    return "; ".join(parts)


class MessagingFeature(BaseFeature):
    """Фича обработки сообщений от пользователей с LLM-агентом."""

    name = "messaging"

    def __init__(self, user_storage, message_copier, feature_manager):
        super().__init__()
        self.user_storage = user_storage
        self.message_copier = message_copier
        self.feature_manager = feature_manager
        self.bot = None
        self.agents_client = None
        self._mass_alert_sent_at = 0.0

    def set_bot(self, bot):
        """Устанавливает экземпляр бота."""
        self.bot = bot

    async def initialize(self) -> None:
        logger.info("[%s] Фича инициализируется", self.name)
        from openai_client.agents_client import AgentsClient

        self.agents_client = AgentsClient(self.user_storage)
        logger.info("[%s] ✅ Agents client initialized", self.name)

    async def teardown(self) -> None:
        logger.info("[%s] Фича остановлена", self.name)

    def register_handlers(self, dp: Dispatcher) -> None:
        pass

    async def handle_chat_message(
        self,
        message: Message,
        state: FSMContext,
        text: str,
        message_id: int = None,
    ):
        """Обработчик всех сообщений (только личный чат с ботом)."""
        if message.chat.type != ChatType.PRIVATE:
            logger.debug(
                "messaging: ignore chat_id=%s type=%s",
                message.chat.id,
                message.chat.type,
            )
            return
        user_id = message.from_user.id
        logger.info("📨 Получено сообщение от %s: %s...", user_id, text[:50])

        async def _dialog() -> None:
            agent_response = await self._get_agent_response_with_retries(
                user_id, text
            )
            if agent_response:
                sent = await self._send_to_user(
                    message, agent_response, inbound_text=text
                )
                if not sent:
                    await self._log_tech_incident(user_id, "agent_send_failed")
                    await message.reply("Что-то пошло не так. Попробуйте еще раз")
            else:
                await self._log_tech_incident(user_id, "agent_response_failed")
                await self._handle_hard_fail(message, user_id, text)

        tg = self.bot.bot if self.bot else None
        if tg:
            async with ChatActionSender.typing(
                message.chat.id,
                tg,
                message.message_thread_id,
            ):
                await _dialog()
        else:
            await _dialog()

    async def _get_agent_response_with_retries(
        self, user_id: int, question: str
    ) -> Optional[str]:
        """До 4 попыток: DS → DS → DS без thinking → OpenAI."""
        last_err: Optional[BaseException] = None
        empty_count = 0
        for attempt, step in enumerate(_RETRY_STEPS, start=1):
            # Без thinking — только после двух пустых ответов; иначе на 3-м шаге
            # при непустых сбоях (таймаут и т.п.) сразу OpenAI.
            use_no_thinking = bool(step.get("thinking") == "disabled")
            use_openai = bool(step.get("prefer_openai"))
            if use_no_thinking and empty_count < 2:
                use_openai = True
                use_no_thinking = False
            try:
                if use_openai:
                    text = await self._get_agent_response(
                        user_id, question, prefer_openai=True
                    )
                elif use_no_thinking:
                    logger.info(
                        "agent retry uid=%s attempt=%s DeepSeek thinking=disabled "
                        "(after %s empty)",
                        user_id,
                        attempt,
                        empty_count,
                    )
                    text = await self._get_agent_response(
                        user_id, question, thinking="disabled"
                    )
                else:
                    text = await self._get_agent_response(user_id, question)
                if text:
                    _record_gen_attempt(True)
                    await self._maybe_clear_mass_failure()
                    return text
                # None/"" — смотрим last_chat_error: таймаут ≠ пустой content
                err_kind = getattr(self.agents_client, "last_chat_error", None)
                finish = getattr(self.agents_client, "last_chat_finish_reason", None)
                if err_kind == "timeout":
                    last_err = asyncio.TimeoutError("DeepSeek timeout")
                elif err_kind == "api_error":
                    last_err = RuntimeError("DeepSeek API error")
                else:
                    # empty content или неизвестно → считаем пустым для thinking=disabled
                    empty_count += 1
                    last_err = RuntimeError(f"empty_response finish={finish}")
                _record_gen_attempt(
                    False,
                    reason=_short_fail_reason(last_err, finish=finish),
                )
            except Exception as e:
                last_err = e
                finish = getattr(self.agents_client, "last_chat_finish_reason", None)
                _record_gen_attempt(
                    False,
                    reason=_short_fail_reason(e, finish=finish),
                )
                logger.error(
                    "❌ Agent attempt %s failed user %s: %s", attempt, user_id, e
                )
            await self._maybe_trip_mass_failure()
            if use_openai:
                break
            delay_idx = min(attempt - 1, len(_RETRY_DELAYS) - 1)
            await asyncio.sleep(_RETRY_DELAYS[delay_idx])
        logger.error(
            "agent all retries failed user=%s err=%s", user_id, last_err
        )
        return None

    async def _maybe_trip_mass_failure(self) -> None:
        rate, fails, total = _gen_error_rate_5m()
        if total < 10 or rate <= 0.30:
            return
        now = time.monotonic()
        if now - self._mass_alert_sent_at < 300:
            return
        self._mass_alert_sent_at = now
        set_background_jobs_paused(True)
        try:
            await self.user_storage.pause_all_pending_replies()
        except Exception:
            pass
        why = _top_fail_reasons_5m()
        why_line = f"\nПричина: {why}." if why else ""
        tg = self.bot.bot if self.bot else None
        if tg:
            tid = int(getattr(config, "TECH_ALERT_TOPIC_ID", 0) or 0) or None
            try:
                await send_admin_html_message(
                    tg,
                    (
                        f"🚨 <b>Массовый сбой генерации</b>\n"
                        f"За 5 мин ошибок: {fails}/{total} ({rate:.0%}).\n"
                        f"Nudge/pending приостановлены."
                        f"{why_line}"
                    ),
                    message_thread_id=tid,
                )
            except Exception as e:
                logger.error("mass failure alert: %s", e)

    async def _maybe_clear_mass_failure(self) -> None:
        if not background_jobs_paused():
            return
        rate, fails, total = _gen_error_rate_5m()
        if total >= 5 and rate < 0.15:
            set_background_jobs_paused(False)
            try:
                await self.user_storage.resume_paused_pending_replies()
            except Exception:
                pass

    async def _handle_hard_fail(
        self, message: Message, user_id: int, text: str
    ) -> None:
        try:
            await message.answer(_SOFT_FAIL_TEXT)
        except Exception as e:
            logger.warning("soft fail notice uid=%s: %s", user_id, e)
        try:
            await self.user_storage.enqueue_pending_reply(
                user_id=user_id,
                chat_id=message.chat.id,
                text=text,
            )
        except Exception as e:
            logger.error("enqueue pending_reply uid=%s: %s", user_id, e)

    async def _get_agent_response(
        self,
        user_id: int,
        question: str,
        *,
        prefer_openai: bool = False,
        thinking: Optional[str] = None,
    ) -> Optional[str]:
        try:
            if not self.agents_client:
                logger.warning("Agents client not initialized")
                return None
            if prefer_openai and hasattr(self.agents_client, "run_openai_fallback"):
                return await self.agents_client.run_openai_fallback(
                    user_message=question,
                    user_id=user_id,
                )
            if thinking:
                return await self.agents_client.run(
                    user_message=question,
                    user_id=user_id,
                    thinking=thinking,  # type: ignore[arg-type]
                )
            return await self.agents_client.run(
                user_message=question,
                user_id=user_id,
            )
        except Exception as e:
            logger.error("❌ Agent response failed for user %s: %s", user_id, e)
            raise

    async def _log_tech_incident(self, user_id: int, kind: str) -> None:
        try:
            await self.user_storage.log_bot_tech_incident(user_id, kind)
        except Exception as e:
            logger.debug("log tech incident uid=%s: %s", user_id, e)

    async def _send_to_user(
        self, message: Message, response: str, *, inbound_text: str = ""
    ) -> bool:
        """Ответ пользователю (HTML). True — доставлено."""
        try:
            body, _ = strip_subscribe_cta(response)
            uid = message.from_user.id if message.from_user else 0
            response_html = await normalize_llm_reply_for_telegram_async(
                body,
                user_id=uid,
                agents_client=self.agents_client,
            )
            await message.reply(response_html, parse_mode=ParseMode.HTML)
            logger.info("✅ Agent response sent to user %s", message.from_user.id)
            return True
        except Exception as e:
            logger.error("❌ Failed to send response to user: %s", e)
            return False
