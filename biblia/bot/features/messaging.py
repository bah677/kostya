# bot/features/messaging.py
import asyncio
import logging
import time
from collections import deque
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
_SOFT_FAIL_TEXT = (
    "Мне нужно немного больше времени, чтобы ответить.\n"
    "Я вернусь к вашему сообщению — не нужно писать заново."
)

# sliding window: (monotonic_ts, ok:bool)
_GEN_ATTEMPTS: Deque[Tuple[float, bool]] = deque(maxlen=500)


def _record_gen_attempt(ok: bool) -> None:
    _GEN_ATTEMPTS.append((time.monotonic(), bool(ok)))


def _gen_error_rate_5m() -> Tuple[float, int, int]:
    now = time.monotonic()
    window = [x for x in _GEN_ATTEMPTS if now - x[0] <= 300.0]
    if not window:
        return 0.0, 0, 0
    fails = sum(1 for _, ok in window if not ok)
    total = len(window)
    return fails / total, fails, total


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
        """3 попытки: 2с / 6с / 15с+OpenAI fallback."""
        last_err: Optional[BaseException] = None
        for attempt, delay in enumerate(_RETRY_DELAYS, start=1):
            try:
                if attempt == 3:
                    text = await self._get_agent_response(
                        user_id, question, prefer_openai=True
                    )
                else:
                    text = await self._get_agent_response(user_id, question)
                if text:
                    _record_gen_attempt(True)
                    await self._maybe_clear_mass_failure()
                    return text
                _record_gen_attempt(False)
                last_err = RuntimeError("empty_response")
            except Exception as e:
                last_err = e
                _record_gen_attempt(False)
                logger.error(
                    "❌ Agent attempt %s failed user %s: %s", attempt, user_id, e
                )
            await self._maybe_trip_mass_failure()
            if attempt < len(_RETRY_DELAYS):
                await asyncio.sleep(delay)
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
