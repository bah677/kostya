# bot/features/messaging.py
import logging
import html
import secrets
import asyncio
from datetime import datetime
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.chat_action import ChatActionSender
from typing import Optional, Tuple
from aiogram import Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from bot.features.base import BaseFeature
from bot.texts import ru_messaging as msg_txt
from bot.utils.admin_channel import send_admin_html_message
from bot.utils.telegram_send import call_with_flood_retry, send_telegram_html_chunks
from bot.utils.user_ui import with_main_menu
from bot.utils.telegram_html import (
    html_to_plain,
    sanitize_telegram_html,
    strip_agent_cta,
    strip_subscribe_cta,
)
from bot.services.sales_agent_cta import (
    analyze_private_history_for_sales_cta,
    apply_sales_cta_policy,
)
from bot.texts.ru_benefit import PROMO_PAYMENT_CALLBACK_408
from bot.services.member_license_context import looks_like_club_link_problem
from config import config
from openai_client.agents_client import DeepSeekTimeoutError

logger = logging.getLogger(__name__)


def _is_forum_thread_missing(exc: Exception) -> bool:
    err = str(exc).lower()
    return "thread not found" in err or "message thread not found" in err


# Временно отключено: динамические inline-кнопки (quick reply) под ответом агента.
# Логика извлечения и отправки ниже сохранена; включить — поставить True.
# Кнопка «Вступить в клуб» (payment_start) по CTA в ответе агента не зависит от этого флага.
ENABLE_AGENT_QUICK_REPLY_INLINE = False


class MessagingFeature(BaseFeature):
    """Фича обработки сообщений от пользователей с агентом OpenAI."""
    
    name = "messaging"
    
    def __init__(self, user_storage, message_copier, feature_manager):
        super().__init__()
        self.user_storage = user_storage
        self.message_copier = message_copier
        self.feature_manager = feature_manager
        self.bot = None
        self.admin_channel_id = config.ADMIN_CHANNEL_ID
        self.admin_topic_id = config.ADMIN_DIALOG_THREAD_ID
        self.agents_client = None
        self.member_agents_client = None
        self.rag_stack = None
        # qr:cache_id:idx -> подписи кнопок (одноразово по нажатию)
        self._quick_reply_cache: dict[str, list[str]] = {}
        self._timeout_retry_tasks: dict[int, asyncio.Task] = {}
        self._timeout_retry_delay_sec = 45
        self._timeout_retry_max_attempts = 2
    
    def set_bot(self, bot):
        """Устанавливает экземпляр бота."""
        self.bot = bot

    def set_rag_stack(self, rag_stack) -> None:
        """Chroma RAG (``RagStack``), поднимается в ``TelegramBotApp.initialize``."""
        self.rag_stack = rag_stack
    
    async def initialize(self) -> None:
        """Инициализация фичи."""
        logger.info(f"[{self.name}] Фича инициализирована")
        
        from openai_client.agents_client import AgentsClient
        from openai_client.member_agents_client import MemberAgentsClient

        self.agents_client = AgentsClient(
            self.user_storage, rag_stack=self.rag_stack
        )
        self.member_agents_client = MemberAgentsClient(
            self.user_storage, rag_stack=self.rag_stack
        )
        if self.rag_stack:
            logger.info(f"[{self.name}] ✅ Sales + member agents + RAG initialized")
        else:
            logger.info(f"[{self.name}] ✅ Sales + member agents initialized (RAG off)")
        await self._drain_stale_pending_admin_responses_once()
    
    async def teardown(self) -> None:
        """Очистка при отключении фичи."""
        for task in self._timeout_retry_tasks.values():
            task.cancel()
        self._timeout_retry_tasks.clear()
        logger.info(f"[{self.name}] Фича остановлена")
    
    def register_handlers(self, dp: Dispatcher) -> None:
        """Регистрирует обработчики."""
        pass

    async def _drain_stale_pending_admin_responses_once(self, *, max_batches: int = 30) -> None:
        """Одноразово дочищает строки pending (раньше продажная доставка шла через цикл фичи)."""
        if not self.bot:
            return
        for _ in range(max_batches):
            pending_responses = await self.user_storage.get_pending_admin_responses(limit=15)
            if not pending_responses:
                break
            logger.warning(
                "📬 Отправляю оставшиеся pending admin_responses (батч из %s)…",
                len(pending_responses),
            )
            for response in pending_responses:
                await self._send_one_pending_admin_response(response)

    async def _send_one_pending_admin_response(self, response: dict) -> None:
        try:
            user_id = response["user_id"]
            message_text = response["message_text"]
            response_id = response["id"]
            reply_text = sanitize_telegram_html(message_text) + "\n\n"
            await self.bot.bot.send_message(
                chat_id=user_id,
                text=reply_text,
                parse_mode=ParseMode.HTML,
            )
            await self.user_storage.update_admin_response_status(response_id, "sent")
            logger.info("✅ Старый pending response %s доставлен user %s", response_id, user_id)
        except Exception as e:
            error_msg = str(e)
            logger.error(
                "❌ Старый pending response %s для user %s: %s",
                response["id"],
                response["user_id"],
                error_msg,
            )
            if "blocked" in error_msg.lower() or "deactivated" in error_msg.lower():
                await self.user_storage.update_admin_response_status(
                    response["id"], "failed", error_msg
                )

    async def _resolve_conversation_user(
        self, message: Message, from_user: Optional[User]
    ) -> User:
        """Кто пишет в диалоге: для исходящего сообщения бота (колбэк по inline) ``message.from_user`` — бот.

        В личке id пользователя = ``message.chat.id``.
        """
        if from_user is not None and not from_user.is_bot:
            return from_user
        if message.from_user is not None and not message.from_user.is_bot:
            return message.from_user
        if message.chat.type == "private":
            uid = message.chat.id
            row = await self.user_storage.get_user(uid)
            fn = (row.get("first_name") or msg_txt.DEFAULT_USER_DISPLAY_NAME) if row else msg_txt.DEFAULT_USER_DISPLAY_NAME
            ln = row.get("last_name") if row else None
            un = row.get("username") if row else None
            return User(
                id=uid,
                is_bot=False,
                first_name=fn,
                last_name=ln,
                username=un,
            )
        return from_user if from_user is not None else message.from_user

    async def handle_chat_message(
        self,
        message: Message,
        state: FSMContext,
        text: str,
        message_id: int = None,
        *,
        from_user: Optional[User] = None,
        from_inline_button: bool = False,
        onboarding_topic_button: bool = False,
    ):
        """Обработчик всех сообщений."""
        actor = await self._resolve_conversation_user(message, from_user)
        user_id = actor.id
        text = (text or "").strip()

        logger.info(f"📨 Получено сообщение от {user_id}: {text[:50]}...")

        if not text:
            await message.answer(msg_txt.MEDIA_EMPTY_HTML)
            return

        await self._maybe_apply_admin_schedule_dm(user_id, text)

        if self.feature_manager:
            followup = self.feature_manager.get("followup")
            if followup and hasattr(followup, "refresh_segment_from_activity"):
                try:
                    await followup.refresh_segment_from_activity(user_id)
                except Exception as e:
                    logger.warning(
                        "Followup segment refresh failed for user %s: %s",
                        user_id,
                        e,
                    )

        # 1. Отправляем сообщение агенту и получаем ответ («печатает…» на время ответа нейросети)
        async with ChatActionSender.typing(
            bot=self.bot.bot,
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
        ):
            agent_response, timed_out = await self._get_agent_response(user_id, text)
            quick_choices: list[str] = []
            body = ""
            wants_cta = False
            wants_promo_week = False
            if agent_response:
                is_member_agent = await self._is_member_agent_user(user_id)
                if is_member_agent:
                    body, wants_cta = strip_subscribe_cta(agent_response)
                else:
                    body, wants_cta, wants_promo_week = strip_agent_cta(agent_response)
                    history = await self.user_storage.get_private_chat_history(
                        user_id, limit=40
                    )
                    assistant_count, first_user_msg = (
                        analyze_private_history_for_sales_cta(history)
                    )
                    if not first_user_msg and text.strip().lower() not in (
                        "/start",
                        "start",
                    ):
                        first_user_msg = text
                    wants_cta, wants_promo_week = apply_sales_cta_policy(
                        wants_subscribe=wants_cta,
                        wants_promo_week=wants_promo_week,
                        assistant_replies_in_history=assistant_count,
                        first_user_message=first_user_msg,
                        current_user_message=text,
                    )
                if ENABLE_AGENT_QUICK_REPLY_INLINE and (
                    not wants_cta
                    and not wants_promo_week
                    and self.agents_client
                    and "?" in body
                ):
                    plain = html_to_plain(body)
                    raw_choices = await self.agents_client.extract_quick_reply_choices(
                        plain,
                        user_id=user_id,
                    )
                    if len(raw_choices) >= 2:
                        quick_choices = raw_choices

        if agent_response:
            # 2. Отправляем ответ пользователю (кнопка клуба и/или быстрые варианты)
            await self._send_to_user(
                message,
                body,
                wants_cta,
                wants_promo_week,
                quick_choices,
                actor=actor,
            )

            await self._maybe_send_fresh_club_invite(user_id, text)

            if self.feature_manager:
                followup = self.feature_manager.get("followup")
                if followup and hasattr(followup, "on_assistant_replied"):
                    try:
                        await followup.on_assistant_replied(user_id)
                    except Exception as e:
                        logger.warning(
                            "Followup on_assistant_replied failed user %s: %s",
                            user_id,
                            e,
                        )

            # 3. Пересылаем в админский топик пару "вопрос → ответ"
            await self._forward_to_admin(
                message,
                text,
                agent_response,
                actor=actor,
                from_inline_button=from_inline_button,
                onboarding_topic_button=onboarding_topic_button,
                member_agent=await self._is_member_agent_user(user_id),
            )
        else:
            if timed_out:
                await message.answer(msg_txt.AGENT_TIMEOUT_RETRY_HTML)
                return
            # Если агент не ответил, пересылаем только вопрос в админку
            await self._forward_to_admin(
                message,
                text,
                None,
                actor=actor,
                from_inline_button=from_inline_button,
                onboarding_topic_button=onboarding_topic_button,
                member_agent=await self._is_member_agent_user(user_id),
            )
            await message.answer(msg_txt.AGENT_NO_REPLY_HTML)

    async def _maybe_apply_admin_schedule_dm(self, user_id: int, text: str) -> None:
        """Правка расписания админом без лицензии (с лицензией — в MemberAgentsClient)."""
        try:
            from bot.services.club_schedule_extractor import text_looks_like_schedule
            from bot.services.club_schedule_service import try_apply_schedule_from_admin_dm

            if not text_looks_like_schedule(text):
                return
            if not await self.user_storage.is_telegram_admin_id(user_id):
                return
            if (
                config.MEMBER_AGENT_ENABLED
                and self.member_agents_client
                and await self.user_storage.user_has_active_license(user_id)
            ):
                return

            client = None
            if self.member_agents_client:
                client = self.member_agents_client.client
            elif self.agents_client:
                client = self.agents_client.client
            if client is None:
                return

            result = await try_apply_schedule_from_admin_dm(
                self.user_storage, client, user_id, text
            )
            if result and result.applied:
                logger.info("schedule admin dm (sales path) uid=%s", user_id)
        except Exception as e:
            logger.warning("schedule admin dm pre-agent uid=%s: %s", user_id, e)

    async def _maybe_send_fresh_club_invite(self, user_id: int, text: str) -> bool:
        """При жалобе на ссылку — новый инвайт отдельным сообщением (member-участники)."""
        if not looks_like_club_link_problem(text):
            return False
        if not self.feature_manager:
            return False
        club = self.feature_manager.get("club_group")
        if not club or not hasattr(club, "send_fresh_club_invite"):
            return False
        try:
            if not await club.user_needs_club_invite(user_id):
                return False
            sent = await club.send_fresh_club_invite(user_id)
            if sent:
                logger.info(
                    "Fresh club invite auto-sent uid=%s (link problem in message)",
                    user_id,
                )
            return sent
        except Exception as e:
            logger.warning("Fresh club invite auto-send uid=%s: %s", user_id, e)
            return False

    async def _is_member_agent_user(self, user_id: int) -> bool:
        return bool(
            config.MEMBER_AGENT_ENABLED
            and self.member_agents_client
            and await self.user_storage.user_has_active_license(user_id)
        )

    async def _resolve_agents_client(self, user_id: int):
        """Sales-агент для лидов; member-агент — для активной лицензии."""
        if (
            config.MEMBER_AGENT_ENABLED
            and self.member_agents_client
            and await self.user_storage.user_has_active_license(user_id)
        ):
            return self.member_agents_client
        return self.agents_client
    
    async def _get_agent_response(self, user_id: int, question: str) -> Tuple[Optional[str], bool]:
        """Получает ответ от агента.

        Возвращает (answer, timed_out).
        """
        try:
            agents_client = await self._resolve_agents_client(user_id)
            if not agents_client:
                logger.warning("Agents client not initialized")
                return None, False
            
            response = await agents_client.run(
                user_message=question,
                user_id=user_id
            )
            if response is None:
                logger.warning("Agents run returned no reply user=%s", user_id)
            return response, False
        except DeepSeekTimeoutError:
            await self._schedule_timeout_retry(user_id=user_id, question=question)
            return None, True
            
        except Exception as e:
            logger.error(f"❌ Agent response failed for user {user_id}: {e}")
            return None, False

    async def _schedule_timeout_retry(self, *, user_id: int, question: str) -> None:
        running = self._timeout_retry_tasks.get(user_id)
        if running and not running.done():
            logger.info(
                "DeepSeek retry already scheduled user=%s; skip duplicate",
                user_id,
            )
            return

        task = asyncio.create_task(
            self._run_timeout_retry(user_id=user_id, question=question),
            name=f"deepseek-timeout-retry-{user_id}",
        )
        self._timeout_retry_tasks[user_id] = task

    async def _run_timeout_retry(self, *, user_id: int, question: str) -> None:
        try:
            agents_client = await self._resolve_agents_client(user_id)
            if not agents_client:
                return
            for attempt in range(1, self._timeout_retry_max_attempts + 1):
                delay = self._timeout_retry_delay_sec * attempt
                logger.warning(
                    "DeepSeek timeout retry scheduled user=%s attempt=%s delay=%ss",
                    user_id,
                    attempt,
                    delay,
                )
                await self.user_storage.log_interaction(
                    user_id=user_id,
                    event_category="llm",
                    event_type="deepseek_timeout_retry_scheduled",
                    data={"attempt": attempt, "delay_sec": delay},
                    source="deepseek",
                    outcome="scheduled",
                )
                await asyncio.sleep(delay)

                latest = await self.user_storage.get_last_private_message(user_id)
                if not latest or latest.get("role") != "user" or latest.get("content") != question:
                    logger.info(
                        "Skip timeout retry user=%s: dialog moved on", user_id
                    )
                    return

                try:
                    reply = await agents_client.run(
                        user_message=question,
                        user_id=user_id,
                    )
                except DeepSeekTimeoutError:
                    continue

                if not reply:
                    return

                body, _ = strip_subscribe_cta(reply)
                safe = sanitize_telegram_html(body)
                sent = await self.bot.bot.send_message(
                    chat_id=user_id,
                    text=safe,
                    parse_mode=ParseMode.HTML,
                )
                if sent and self.message_copier:
                    await self.message_copier.save_outgoing(
                        message=sent,
                        source="assistant_retry",
                        subtype="agent_timeout_retry",
                    )
                await self.user_storage.log_interaction(
                    user_id=user_id,
                    event_category="llm",
                    event_type="deepseek_timeout_retry_sent",
                    data={"attempt": attempt},
                    source="deepseek",
                    outcome="success",
                )
                return
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type="deepseek_timeout_retry_exhausted",
                data={"max_attempts": self._timeout_retry_max_attempts},
                source="deepseek",
                outcome="error",
            )
            await self._forward_timeout_retry_exhausted(user_id=user_id, question=question)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("DeepSeek timeout retry failed user=%s: %s", user_id, e)
            await self.user_storage.log_interaction(
                user_id=user_id,
                event_category="llm",
                event_type="deepseek_timeout_retry_error",
                data={"error": str(e)},
                source="deepseek",
                outcome="error",
            )
        finally:
            self._timeout_retry_tasks.pop(user_id, None)

    async def _forward_timeout_retry_exhausted(self, *, user_id: int, question: str) -> None:
        escaped_question = html.escape(question or "")
        row = await self.user_storage.get_user(user_id)
        display_name = html.escape((row or {}).get("first_name") or msg_txt.DEFAULT_USER_DISPLAY_NAME)
        username = (row or {}).get("username")
        username_part = f"(@{html.escape(username)})" if username else msg_txt.ADMIN_NO_USERNAME

        identity_header = msg_txt.admin_identity_header(
            ts=datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
            user_disp=display_name,
            un_part=username_part,
            user_id=user_id,
            start_src_esc=html.escape(await self.user_storage.get_first_start_source_display(user_id)),
        )

        forum_topic = None
        delivered_to_dialogs = False
        forum_group = config.DIALOG_FORUM_GROUP_ID
        if forum_group:
            user = User(
                id=user_id,
                is_bot=False,
                first_name=(row or {}).get("first_name") or msg_txt.DEFAULT_USER_DISPLAY_NAME,
                last_name=(row or {}).get("last_name"),
                username=username,
            )
            forum_topic = await self._resolve_forum_topic(user)
            if forum_topic is not None:
                preamble = f"⚠️ <b>Авто-ретрай DeepSeek исчерпан</b>\n\n{identity_header}"
                ok = await self._send_dialog_to_forum(
                    escaped_question=escaped_question,
                    answer=None,
                    preamble=preamble,
                    topic_id=forum_topic,
                )
                if ok:
                    logger.warning(
                        "Timeout exhausted: question forwarded to personal forum topic user=%s",
                        user_id,
                    )
                    delivered_to_dialogs = True

        # legacy fallback в общий топик диалогов, если персональный не сработал
        if not delivered_to_dialogs and self.admin_channel_id:
            text = msg_txt.admin_legacy_dialog_no_answer(
                source_note="⚠️ <b>Авто-ретрай DeepSeek исчерпан</b>\n\n",
                identity_header=identity_header,
                escaped_question=escaped_question,
            )
            await send_admin_html_message(
                self.bot.bot,
                text,
                thread_id=self.admin_topic_id if self.admin_topic_id > 0 else None,
            )

        # отдельный тикет в ТП-топик
        support_tid = config.SUPPORT_THREAD_ID if config.SUPPORT_THREAD_ID > 0 else None
        if self.admin_channel_id:
            alert_text = msg_txt.admin_timeout_retry_exhausted_alert(
                user_id=user_id,
                user_display=display_name,
                username_part=username_part,
                escaped_question=escaped_question,
            )
            await send_admin_html_message(
                self.bot.bot,
                alert_text,
                thread_id=support_tid,
            )

    @staticmethod
    def _truncate_button_label(text: str, max_len: int = 58) -> str:
        """Лимит подписи inline-кнопки Telegram (до 64 символов)."""
        t = text.strip()
        if len(t) <= max_len:
            return t
        return t[: max_len - 1] + "…"

    def _store_quick_reply_choices(self, cache_id: str, choices: list[str]) -> None:
        if len(self._quick_reply_cache) > 800:
            self._quick_reply_cache.clear()
        self._quick_reply_cache[cache_id] = choices

    async def handle_quick_reply_callback(
        self, callback: CallbackQuery, state: FSMContext
    ) -> None:
        """Нажатие на вариант ответа — отправляем тот же текст, как будто пользователь написал сам."""
        data = callback.data or ""
        parts = data.split(":")
        if len(parts) != 3 or parts[0] != "qr":
            await callback.answer(msg_txt.QUICK_REPLY_INVALID_DATA_ALERT, show_alert=True)
            return
        _, cache_id, idx_s = parts
        try:
            idx = int(idx_s)
        except ValueError:
            await callback.answer(msg_txt.QUICK_REPLY_ERROR_ALERT, show_alert=True)
            return

        choices = self._quick_reply_cache.pop(cache_id, None)
        if not choices or idx < 0 or idx >= len(choices):
            await callback.answer(
                msg_txt.QUICK_REPLY_STALE_ALERT,
                show_alert=True,
            )
            return

        await callback.answer()
        chosen = choices[idx]
        logger.info(
            "Quick reply picked user=%s idx=%s text=%s",
            callback.from_user.id,
            idx,
            chosen[:80],
        )
        await self.message_copier.save_synthetic_private_user_text(
            user_id=callback.from_user.id,
            chat_id=callback.message.chat.id,
            content=chosen,
            callback_query_id=str(callback.id),
            callback_data=data,
            subtype="quick_reply_pick",
        )
        await self.handle_chat_message(
            callback.message,
            state,
            chosen,
            message_id=None,
            from_user=callback.from_user,
            from_inline_button=True,
        )

    async def _send_to_user(
        self,
        message: Message,
        body: str,
        wants_cta: bool,
        wants_promo_week: bool,
        quick_choices: list[str],
        *,
        actor: User,
    ) -> None:
        """Отправляет ответ пользователю (HTML); кнопка клуба и/или быстрые варианты ответа."""
        try:
            response_html = sanitize_telegram_html(body)
            if not (response_html or "").strip():
                logger.warning(
                    "sanitize_telegram_html produced empty reply user=%s wants_cta=%s",
                    actor.id,
                    wants_cta,
                )
                response_html = (body or "").strip()
            if not (response_html or "").strip():
                response_html = msg_txt.AGENT_NO_REPLY_HTML
            rows: list[list[InlineKeyboardButton]] = []

            if wants_promo_week:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=msg_txt.BTN_PROMO_WEEK,
                            callback_data=PROMO_PAYMENT_CALLBACK_408,
                            style="success",
                        )
                    ]
                )
            elif wants_cta:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=msg_txt.BTN_JOIN_CLUB,
                            callback_data="payment_start",
                            style="success",
                        )
                    ]
                )
            elif quick_choices:
                cache_id = secrets.token_hex(4)
                self._store_quick_reply_choices(cache_id, quick_choices)
                for i in range(0, len(quick_choices), 2):
                    row: list[InlineKeyboardButton] = []
                    for j in (i, i + 1):
                        if j < len(quick_choices):
                            row.append(
                                InlineKeyboardButton(
                                    text=self._truncate_button_label(quick_choices[j]),
                                    callback_data=f"qr:{cache_id}:{j}",
                                )
                            )
                    rows.append(row)

            keyboard = with_main_menu(rows) if rows else None

            sent = await message.answer(
                response_html,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            if sent and self.message_copier:
                row_id = await self.message_copier.save_outgoing(
                    message=sent,
                    source="assistant",
                    subtype="agent_reply",
                )
                if row_id is None:
                    logger.warning(
                        "agent reply not saved to messages user=%s mid=%s",
                        actor.id,
                        sent.message_id,
                    )
        except Exception as e:
            logger.exception(
                "send_to_user failed user=%s: %s",
                actor.id,
                e,
            )
    
    # ==================== ПЕРСОНАЛЬНЫЕ ТОПИКИ ДИАЛОГОВ ====================

    async def _resolve_forum_topic(self, user: User) -> Optional[int]:
        """Получить или создать персональный топик в DIALOG_FORUM_GROUP_ID.

        Возвращает topic_id или None (тогда вызывающий код откатится на legacy).
        """
        forum_group = config.DIALOG_FORUM_GROUP_ID
        if not forum_group:
            return None

        user_id = user.id
        topic_id = await self.user_storage.get_dialog_topic_id(user_id)

        if topic_id is not None:
            if await self._forum_topic_is_alive(forum_group, topic_id):
                return topic_id
            logger.warning(
                "Топик %s для user %s недоступен — пересоздаю",
                topic_id, user_id,
            )

        new_topic_id = await self._create_forum_topic(forum_group, user)
        if new_topic_id is None:
            return None
        await self.user_storage.upsert_dialog_topic(user_id, new_topic_id)
        return new_topic_id

    def _telegram_bot(self):
        """Aiogram Bot: set_bot передаёт TelegramBotApp, не сам Bot."""
        app = self.bot
        return app.bot if app is not None and hasattr(app, "bot") else app

    async def _forum_topic_is_alive(self, chat_id: int, topic_id: int) -> bool:
        """Проверяем топик так же строго, как send_message (не send_chat_action)."""
        tg = self._telegram_bot()
        if tg is None:
            return False
        try:
            probe = await tg.send_message(
                chat_id=chat_id,
                message_thread_id=topic_id,
                text="\u2063",
                disable_notification=True,
            )
            try:
                await tg.delete_message(chat_id=chat_id, message_id=probe.message_id)
            except Exception:  # noqa: BLE001
                pass
            return True
        except TelegramBadRequest as e:
            if _is_forum_thread_missing(e):
                return False
            logger.warning("forum_topic_is_alive unexpected error: %s", e)
            return False
        except Exception as e:
            logger.warning("forum_topic_is_alive error: %s", e)
            return False

    async def _create_forum_topic(self, chat_id: int, user: User) -> Optional[int]:
        un = f" @{user.username}" if user.username else ""
        name = f"{user.full_name}{un} [{user.id}]"
        name = name[:128]
        try:
            result = await call_with_flood_retry(
                lambda: self._telegram_bot().create_forum_topic(
                    chat_id=chat_id,
                    name=name,
                ),
                log_prefix="create_forum_topic",
            )
            logger.info(
                "Создан форум-топик '%s' (topic_id=%s) для user %s",
                name, result.message_thread_id, user.id,
            )
            return result.message_thread_id
        except Exception as e:
            logger.error("Ошибка создания форум-топика для user %s: %s", user.id, e)
            return None

    # ==================== ПЕРЕСЫЛКА В АДМИНКУ ====================

    async def _forward_to_admin(
        self,
        message: Message,
        question: str,
        answer: str = None,
        *,
        actor: Optional[User] = None,
        from_inline_button: bool = False,
        onboarding_topic_button: bool = False,
        member_agent: bool = False,
    ):
        """Пересылает пару вопрос → ответ в персональный топик (если настроен) или в общий админ-топик."""
        try:
            user = await self._resolve_conversation_user(message, actor)
            if onboarding_topic_button:
                escaped_question = html.escape(msg_txt.admin_onboarding_button_question(question))
            else:
                escaped_question = html.escape(question)

            source_note = ""
            if member_agent:
                source_note += msg_txt.ADMIN_SOURCE_MEMBER_AGENT_NOTE
            if from_inline_button and not onboarding_topic_button:
                source_note += msg_txt.ADMIN_SOURCE_INLINE_NOTE

            # Формируем тело сообщения
            ts = message.date.strftime("%d.%m.%Y %H:%M:%S")
            start_src_esc = html.escape(
                await self.user_storage.get_first_start_source_display(user.id)
            )
            user_disp = html.escape(user.full_name or "")
            un_part = (
                f"(@{html.escape(user.username)})"
                if user.username
                else msg_txt.ADMIN_NO_USERNAME
            )
            identity_header = msg_txt.admin_identity_header(
                ts=ts,
                user_disp=user_disp,
                un_part=un_part,
                user_id=user.id,
                start_src_esc=start_src_esc,
            )

            # Попробуем персональный топик
            forum_topic = await self._resolve_forum_topic(user)

            if forum_topic is not None:
                preamble = f"{source_note}{identity_header}"
                for attempt in range(2):
                    ok = await self._send_dialog_to_forum(
                        escaped_question,
                        answer,
                        preamble,
                        forum_topic,
                        member_agent=member_agent,
                    )
                    if ok:
                        logger.info(
                            "✅ Dialog forwarded to personal topic for user %s",
                            user.id,
                        )
                        return
                    if attempt == 0:
                        logger.warning(
                            "Персональный топик user %s: topic_id=%s недоступен, "
                            "сбрасываю маппинг и пересоздаю",
                            user.id,
                            forum_topic,
                        )
                        await self.user_storage.clear_dialog_topic(user.id)
                        forum_topic = await self._resolve_forum_topic(user)
                        if forum_topic is None:
                            break
                        continue
                    break
                logger.warning(
                    "Персональный топик user %s: отправка не удалась, fallback",
                    user.id,
                )

            # Fallback: старая логика — общий админ-топик
            if not self.admin_channel_id:
                logger.warning("⚠️ Ни DIALOG_FORUM_GROUP_ID, ни ADMIN_CHANNEL_ID не настроены")
                return

            if answer:
                answer_body, _ = strip_subscribe_cta(answer)
                escaped_answer = sanitize_telegram_html(answer_body)
                notification_text = msg_txt.admin_legacy_dialog_with_answer(
                    source_note=source_note,
                    identity_header=identity_header,
                    escaped_question=escaped_question,
                    escaped_answer=escaped_answer,
                    member_agent=member_agent,
                )
            else:
                notification_text = msg_txt.admin_legacy_dialog_no_answer(
                    source_note=source_note,
                    identity_header=identity_header,
                    escaped_question=escaped_question,
                )

            ok = await send_admin_html_message(
                self.bot.bot,
                notification_text,
                thread_id=self.admin_topic_id if self.admin_topic_id > 0 else None,
            )
            if ok:
                logger.info("✅ Dialog forwarded to admin (legacy) for user %s", user.id)
            else:
                logger.error("❌ Failed to forward dialog to admin channel")
        except Exception as e:
            logger.error("❌ Ошибка пересылки в админский топик: %s", e)

    async def _send_dialog_to_forum(
        self,
        escaped_question: str,
        answer: Optional[str],
        preamble: str,
        topic_id: int,
        *,
        member_agent: bool = False,
    ) -> bool:
        """Отправляет сообщение в персональный форум-топик."""
        forum_group = config.DIALOG_FORUM_GROUP_ID
        try:
            if answer:
                answer_body, _ = strip_subscribe_cta(answer)
                escaped_answer = sanitize_telegram_html(answer_body)
                text = msg_txt.admin_forum_dialog_with_answer(
                    preamble=preamble,
                    escaped_question=escaped_question,
                    escaped_answer=escaped_answer,
                    member_agent=member_agent,
                )
            else:
                text = msg_txt.admin_forum_dialog_no_answer(
                    preamble=preamble,
                    escaped_question=escaped_question,
                )

            tg = self._telegram_bot()
            await send_telegram_html_chunks(
                tg,
                forum_group,
                text,
                message_thread_id=topic_id,
                sanitize=False,
            )
            return True
        except TelegramBadRequest as e:
            if _is_forum_thread_missing(e):
                logger.warning(
                    "_send_dialog_to_forum: topic %s not found in chat %s",
                    topic_id,
                    forum_group,
                )
            else:
                logger.error(
                    "_send_dialog_to_forum topic=%s chat=%s: %s",
                    topic_id,
                    forum_group,
                    e,
                )
            return False
        except Exception as e:
            logger.error(
                "_send_dialog_to_forum topic=%s chat=%s: %s",
                topic_id,
                forum_group,
                e,
            )
            return False
