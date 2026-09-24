# bot/features/messaging.py
import asyncio
import html
import logging
import time
from collections import Counter, deque
from typing import Deque, List, Optional, Tuple

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

# sliding window: (monotonic_ts, ok:bool) — ок = итоговый ответ пользователю, не промежуточная попытка
_GEN_OUTCOMES: Deque[Tuple[float, bool]] = deque(maxlen=500)
# заглушки: (monotonic_ts, user_id, reason)
_STUB_EVENTS: Deque[Tuple[float, int, str]] = deque(maxlen=200)
# fallback DS→OpenAI: (monotonic_ts,)
_FALLBACK_EVENTS: Deque[float] = deque(maxlen=500)

_COOLDOWN_FALLBACK_SEC = 900.0  # 15 мин
_COOLDOWN_BALANCE_SEC = 1800.0  # 30 мин
_COOLDOWN_STUB_SEC = 120.0  # 2 мин (дайджест)
_COOLDOWN_MASS_SEC = 300.0


def _record_dialog_outcome(ok: bool) -> None:
    _GEN_OUTCOMES.append((time.monotonic(), bool(ok)))


def _dialog_fail_rate_5m() -> Tuple[float, int, int]:
    now = time.monotonic()
    window = [x for x in _GEN_OUTCOMES if now - x[0] <= 300.0]
    if not window:
        return 0.0, 0, 0
    fails = sum(1 for _, ok in window if not ok)
    total = len(window)
    return fails / total, fails, total


def _is_balance_error(msg: str) -> bool:
    low = (msg or "").lower()
    if "нет денег" in low:
        return True
    if "402" in low and ("balance" in low or "insufficient" in low or "баланс" in low):
        return True
    if "insufficient balance" in low:
        return True
    if "insufficient_quota" in low or "exceeded your current quota" in low:
        return True
    if "billing_not_active" in low or "billing hard limit" in low:
        return True
    if "payment_required" in low:
        return True
    return False


def _short_fail_reason(
    err: Optional[BaseException],
    *,
    finish: Optional[str] = None,
    provider: str = "DeepSeek",
) -> str:
    if err is None and not finish:
        return "неизвестно"
    msg = (str(err) if err else "").strip()
    low = msg.lower()
    fin = (finish or "").strip().lower()
    prov = provider or "API"
    if "empty_response" in low or (not msg and fin):
        if fin == "length" or "finish=length" in low:
            return f"{prov}: пустой ответ (finish=length — лимит токенов/reasoning)"
        if fin:
            return f"{prov}: пустой ответ (finish={finish})"
        return f"{prov}: пустой ответ"
    if "timeout" in low or isinstance(err, asyncio.TimeoutError):
        return f"таймаут {prov}"
    if "429" in low or "rate limit" in low:
        return f"лимит запросов {prov} (429)"
    if _is_balance_error(msg):
        return f"нет денег на балансе {prov}"
    if "503" in low or "502" in low or "500" in low or "overloaded" in low:
        return f"{prov} временно недоступен (5xx)"
    if "connect" in low or "connection" in low:
        return f"сеть/соединение с {prov}"
    name = type(err).__name__ if err else "Error"
    brief = msg.replace("\n", " ")[:100] if msg else name
    return f"{prov}: {brief}" if msg else f"{prov}: {name}"


def _tech_topic_id() -> Optional[int]:
    tid = int(getattr(config, "TECH_ALERT_TOPIC_ID", 0) or 0)
    return tid or None


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
        self._cooldown_until = {
            "fallback": 0.0,
            "balance_DeepSeek": 0.0,
            "balance_OpenAI": 0.0,
            "stub": 0.0,
            "mass": 0.0,
        }
        self._fallback_since_alert = 0
        self._stub_since_alert = 0
        self._stub_users_since_alert: set[int] = set()

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
            agent_response, meta = await self._get_agent_response_with_retries(
                user_id, text
            )
            if agent_response:
                sent = await self._send_to_user(
                    message, agent_response, inbound_text=text
                )
                if not sent:
                    _record_dialog_outcome(False)
                    await self._log_tech_incident(user_id, "agent_send_failed")
                    await self._notify_user_stub(
                        user_id,
                        reason="ответ сгенерирован, но не доставлен в Telegram",
                    )
                    await message.reply("Что-то пошло не так. Попробуйте еще раз")
                else:
                    _record_dialog_outcome(True)
                    if meta.get("via_openai_fallback"):
                        await self._notify_openai_fallback(
                            reasons=meta.get("deepseek_reasons") or [],
                        )
            else:
                _record_dialog_outcome(False)
                await self._log_tech_incident(user_id, "agent_response_failed")
                await self._handle_hard_fail(
                    message,
                    user_id,
                    text,
                    last_reason=meta.get("last_reason") or "все попытки генерации провалились",
                )

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
    ) -> Tuple[Optional[str], dict]:
        """До 4 попыток: DS → DS → DS без thinking → OpenAI.

        Возвращает (текст|None, meta).
        """
        last_err: Optional[BaseException] = None
        last_reason = ""
        empty_count = 0
        deepseek_reasons: List[str] = []
        deepseek_failed = False
        via_openai = False

        for attempt, step in enumerate(_RETRY_STEPS, start=1):
            use_no_thinking = bool(step.get("thinking") == "disabled")
            use_openai = bool(step.get("prefer_openai"))
            if use_no_thinking and empty_count < 2:
                use_openai = True
                use_no_thinking = False
            provider = "OpenAI" if use_openai else "DeepSeek"
            try:
                if use_openai:
                    via_openai = True
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
                    return text, {
                        "via_openai_fallback": via_openai and deepseek_failed,
                        "deepseek_reasons": deepseek_reasons,
                        "last_reason": last_reason,
                    }
                err_kind = getattr(self.agents_client, "last_chat_error", None)
                finish = getattr(self.agents_client, "last_chat_finish_reason", None)
                detail = getattr(self.agents_client, "last_chat_error_detail", None)
                if err_kind == "timeout":
                    last_err = asyncio.TimeoutError(f"{provider} timeout")
                elif err_kind == "api_error":
                    last_err = RuntimeError(detail or f"{provider} API error")
                else:
                    empty_count += 1
                    last_err = RuntimeError(f"empty_response finish={finish}")
                last_reason = _short_fail_reason(
                    last_err, finish=finish, provider=provider
                )
            except Exception as e:
                last_err = e
                finish = getattr(self.agents_client, "last_chat_finish_reason", None)
                last_reason = _short_fail_reason(e, finish=finish, provider=provider)
                logger.error(
                    "❌ Agent attempt %s failed user %s: %s", attempt, user_id, e
                )

            if _is_balance_error(last_reason) or _is_balance_error(str(last_err or "")):
                await self._notify_balance(provider, last_reason)

            if not use_openai:
                deepseek_failed = True
                deepseek_reasons.append(last_reason)

            if use_openai:
                break
            delay_idx = min(attempt - 1, len(_RETRY_DELAYS) - 1)
            await asyncio.sleep(_RETRY_DELAYS[delay_idx])

        logger.error(
            "agent all retries failed user=%s err=%s", user_id, last_err
        )
        return None, {
            "via_openai_fallback": False,
            "deepseek_reasons": deepseek_reasons,
            "last_reason": last_reason or str(last_err or "unknown"),
        }

    async def _send_tech_alert(self, text: str) -> None:
        tg = self.bot.bot if self.bot else None
        if not tg:
            return
        try:
            await send_admin_html_message(
                tg, text, message_thread_id=_tech_topic_id()
            )
        except Exception as e:
            logger.error("tech alert send: %s", e)

    def _cooldown_ok(self, key: str, sec: float) -> bool:
        now = time.monotonic()
        if now < self._cooldown_until.get(key, 0.0):
            return False
        self._cooldown_until[key] = now + sec
        return True

    async def _notify_openai_fallback(self, *, reasons: List[str]) -> None:
        """DeepSeek упал, ответ всё же ушёл через OpenAI."""
        _FALLBACK_EVENTS.append(time.monotonic())
        self._fallback_since_alert += 1
        if not self._cooldown_ok("fallback", _COOLDOWN_FALLBACK_SEC):
            return
        n = self._fallback_since_alert
        self._fallback_since_alert = 0
        why = ""
        if reasons:
            top = Counter(reasons).most_common(2)
            why = "; ".join(f"{r} ×{c}" if c > 1 else r for r, c in top)
        why_line = f"\nПочему DeepSeek: {html.escape(why)}" if why else ""
        await self._send_tech_alert(
            "🟠 <b>DeepSeek недоступен — ответы идут через OpenAI</b>\n"
            f"За период: <b>{n}</b> диалог(ов) спасены запасным путём."
            f"{why_line}\n"
            "Люди ответы получают. Если долго не чинится DeepSeek — "
            "проверьте баланс/статус API."
        )

    async def _notify_balance(self, provider: str, reason: str) -> None:
        key = f"balance_{provider}"
        if not self._cooldown_ok(key, _COOLDOWN_BALANCE_SEC):
            return
        if provider == "DeepSeek":
            tip = "Пополните баланс на platform.deepseek.com"
        else:
            tip = "Проверьте billing/quota на platform.openai.com"
        await self._send_tech_alert(
            f"💳 <b>Нет денег на балансе {html.escape(provider)}</b>\n"
            f"{html.escape(reason or 'Insufficient Balance / quota')}\n"
            f"{tip}"
        )

    async def _notify_user_stub(self, user_id: int, *, reason: str) -> None:
        """Юзер реально получил заглушку — ответа модели нет."""
        _STUB_EVENTS.append((time.monotonic(), user_id, reason))
        self._stub_since_alert += 1
        self._stub_users_since_alert.add(int(user_id))
        await self._maybe_trip_mass_on_stubs()
        if not self._cooldown_ok("stub", _COOLDOWN_STUB_SEC):
            return
        n = self._stub_since_alert
        users = sorted(self._stub_users_since_alert)
        self._stub_since_alert = 0
        self._stub_users_since_alert = set()
        sample = ", ".join(str(u) for u in users[:8])
        more = f" (+ещё {len(users) - 8})" if len(users) > 8 else ""
        await self._send_tech_alert(
            "🔴 <b>Юзеры не получают ответ — ушла заглушка</b>\n"
            f"За ~2 мин: <b>{n}</b> заглушк(и), людей: <b>{len(users)}</b>\n"
            f"uid: <code>{html.escape(sample)}{html.escape(more)}</code>\n"
            f"Последняя причина: {html.escape(reason)}\n"
            "Нужно чинить генерацию (DeepSeek и/или OpenAI) — "
            "это уже не «тихо ушли на запасной путь»."
        )

    async def _maybe_trip_mass_on_stubs(self) -> None:
        """Пауза nudge/pending, если много реальных заглушек за 5 мин."""
        rate, fails, total = _dialog_fail_rate_5m()
        now = time.monotonic()
        stubs_5m = sum(1 for ts, _, _ in _STUB_EVENTS if now - ts <= 300.0)
        if stubs_5m < 5 and not (total >= 8 and rate > 0.40):
            return
        if now < self._cooldown_until.get("mass", 0.0):
            return
        self._cooldown_until["mass"] = now + _COOLDOWN_MASS_SEC
        set_background_jobs_paused(True)
        try:
            await self.user_storage.pause_all_pending_replies()
        except Exception:
            pass
        await self._send_tech_alert(
            "🚨 <b>Массово не отвечаем людям</b>\n"
            f"Заглушек за 5 мин: <b>{stubs_5m}</b>; "
            f"провал диалогов: {fails}/{total} ({rate:.0%}).\n"
            "Nudge/pending приостановлены, пока генерация не оживёт."
        )

    async def _maybe_clear_mass_failure(self) -> None:
        if not background_jobs_paused():
            return
        rate, fails, total = _dialog_fail_rate_5m()
        if total >= 5 and rate < 0.15:
            set_background_jobs_paused(False)
            try:
                await self.user_storage.resume_paused_pending_replies()
            except Exception:
                pass

    async def _handle_hard_fail(
        self,
        message: Message,
        user_id: int,
        text: str,
        *,
        last_reason: str,
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
        await self._notify_user_stub(user_id, reason=last_reason)

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
            await self._maybe_clear_mass_failure()
            return True
        except Exception as e:
            logger.error("❌ Failed to send response to user: %s", e)
            return False
