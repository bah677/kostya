# bot/features/followup.py
import logging
import asyncio
import html
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.utils.telegram_html import sanitize_telegram_html, strip_subscribe_cta
from bot.utils.admin_channel import send_admin_html_message
from bot.utils.user_ui import with_main_menu

from bot.features.base import BaseFeature
from bot.texts.ru_benefit import PROMO_PAYMENT_CALLBACK_408
from bot.texts import ru_followup as followup_txt
from bot.texts.prompts.followup_ai import (
    FOLLOWUP_PROMPT_GENERIC,
    FOLLOWUP_PROMPT_STATUS_101,
    FOLLOWUP_PROMPT_STATUS_201,
)
from bot.followup_segments import (
    COLD_SEGMENTS,
    SEG_CART,
    SEG_ENGAGED,
    SEG_ORGANIC_COLD,
    SEG_REFUSED,
    SEG_SENSITIVE,
    SEG_STUCK_DIALOG,
    classify_from_signals,
    pick_topic_snippet,
)
from bot.services.stuck_dialog_pipeline import run_full_stuck_pipeline
from config import config

logger = logging.getLogger(__name__)


class FollowupFeature(BaseFeature):
    """
    Фича дожима: отправляет напоминания пользователям, которые:
    - Статус 101: создали бота, но не создали заказ (через 30 мин)
    - Статус 201: создали заказ, но не оплатили (через 30 мин)
    """
    
    name = "followup"
    
    # Статусы
    STATUS_NO_FOLLOWUP = 0
    
    # Статусы ожидания заказа
    STATUS_WAITING_ORDER = 101           # ожидание заказа
    STATUS_WAITING_ORDER_REMINDED = 102  # напоминание отправлено
    STATUS_WAITING_ORDER_FOREVER = 103   # ожидание заказа (бесконечно, после вечернего сообщения)
    
    # Статусы ожидания оплаты
    STATUS_WAITING_PAYMENT = 201          # ожидание оплаты (первое напоминание)
    STATUS_WAITING_PAYMENT_REMINDED = 202  # второе напоминание (+24 ч)
    STATUS_PAYMENT_FOLLOWUP_DONE = 203    # цепочка по оплате завершена

    # Сегмент B: диалог с агентом (пинг через 48 ч, статус 110)
    STATUS_WAITING_ENGAGED = 110
    STATUS_WAITING_ENGAGED_REMINDED = 111

    # Сегмент B1: «застряли в диалоге» (24–48 ч после ответа ассистента)
    STATUS_WAITING_STUCK = 120
    STATUS_STUCK_PING_SENT = 121
    STATUS_STUCK_DONE = 122

    CB_STUCK_GET_ANSWER = "followup_stuck_get_answer"
    CB_PAYMENT_STUCK_S1 = "payment_start_stuck_dialog_s1"

    STUCK_DELAY_MIN_MINUTES = 1440   # 24 ч
    STUCK_DELAY_MAX_MINUTES = 2880   # 48 ч

    STUCK_CTA_TEXT = followup_txt.STUCK_CTA_TEXT

    # Финальные статусы
    STATUS_FINAL_PAID = 901               # успешно оплатил
    STATUS_FINAL_SENSITIVE = 997          # тяжёлая тема — без дожима (фаза 1)
    STATUS_FINAL_REFUSED = 998            # явный отказ
    STATUS_FINAL_BLOCKED = 999            # заблокировал бота
    STATUS_ENGAGED_DONE = 112             # пинг engaged отправлен

    # Inline для статусов 201/202 (не из БД)
    CB_PAYMENT_STANDARD = "payment_start"
    CB_PAYMENT_PROMO_WEEK = PROMO_PAYMENT_CALLBACK_408
    
    # Маркер для AI-генерации
    AI_GENERATE_MARKER = "__AI_GENERATE__"
    
    def __init__(
        self,
        user_storage,
        bot: Bot,
        feature_manager=None,
        message_copier=None,
    ):
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot
        self.feature_manager = feature_manager
        self.message_copier = message_copier
        self.followup_storage = None
        self.message_cache: Dict[int, Dict[str, Any]] = {}
        self.cache_updated_at = None
        self.cache_ttl = 600  # 10 минута
        self.is_running = False
        self.task: Optional[asyncio.Task] = None
        self.scheduler: Optional[AsyncIOScheduler] = None
        self._processing_lock = asyncio.Lock()
        self._processing_users: set = set()
        self.rag_stack = None
        self.llm_client = None
        self._stuck_prep_inflight: set = set()

    def set_rag_stack(self, rag_stack) -> None:
        self.rag_stack = rag_stack

    def set_llm_client(self, client) -> None:
        self.llm_client = client

    def _stuck_dialog_enabled(self) -> bool:
        return bool(
            getattr(config, "FOLLOWUP_STUCK_DIALOG_ENABLED", False)
            and self.rag_stack is not None
            and self.llm_client is not None
        )

    def _personalize(self, template: str, user_id: int, user_data: Optional[Dict[str, Any]]) -> str:
        name = ""
        if user_data:
            name = (user_data.get("first_name") or "").strip()
        if not name:
            name = followup_txt.DEFAULT_FIRST_NAME
        return template.replace("{имя}", html.escape(name))

    def _keyboard_for_status(self, status: int) -> Optional[InlineKeyboardMarkup]:
        if status == self.STATUS_WAITING_STUCK:
            return with_main_menu(
                [
                    [
                        InlineKeyboardButton(
                            text=followup_txt.BTN_STUCK_GET_ANSWER,
                            callback_data=self.CB_STUCK_GET_ANSWER,
                        )
                    ]
                ]
            )
        if status in (
            self.STATUS_WAITING_PAYMENT,
            self.STATUS_WAITING_PAYMENT_REMINDED,
        ):
            return with_main_menu(
                [
                    [
                        InlineKeyboardButton(
                            text=followup_txt.BTN_PAYMENT_STANDARD,
                            callback_data=self.CB_PAYMENT_STANDARD,
                            style="success",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=followup_txt.BTN_PAYMENT_PROMO_WEEK,
                            callback_data=self.CB_PAYMENT_PROMO_WEEK,
                            style="success",
                        )
                    ],
                ]
            )
        return None

    async def _apply_refusal_if_needed(self, user_id: int) -> bool:
        """True, если пользователь в отказе — дальше дожим не шлём."""
        if await self.followup_storage.user_has_refusal_in_private_chat(user_id):
            await self.followup_storage.set_followup_state(
                user_id, self.STATUS_FINAL_REFUSED
            )
            logger.info("Followup: user %s → 998 (refusal in chat)", user_id)
            return True
        return False

    async def _should_skip_order_engagement_ping(self, user_id: int) -> bool:
        """Не перебивать активный диалог с агентом (фаза 0, дублирует сегмент engaged)."""
        state = await self.followup_storage.get_followup_state(user_id)
        if state.get("segment") in (SEG_ENGAGED, SEG_STUCK_DIALOG):
            return True
        st = state.get("status") or 0
        if st in (
            self.STATUS_WAITING_STUCK,
            self.STATUS_STUCK_PING_SENT,
        ):
            return True
        n = await self.followup_storage.count_meaningful_private_user_messages(
            user_id, hours=24
        )
        return n >= 2

    def _personalize_with_topic(
        self,
        template: str,
        user_id: int,
        user_data: Optional[Dict[str, Any]],
        topic: Optional[str],
    ) -> str:
        text = self._personalize(template, user_id, user_data)
        snippet = pick_topic_snippet(topic or "") or followup_txt.DEFAULT_TOPIC_SNIPPET
        return text.replace("{тема}", html.escape(snippet))

    def _personalize_stuck_ping(
        self,
        template: str,
        user_id: int,
        user_data: Optional[Dict[str, Any]],
        stuck_context: Optional[Dict[str, Any]],
    ) -> str:
        topic = followup_txt.DEFAULT_TOPIC_SNIPPET
        if stuck_context:
            analysis = stuck_context.get("analysis") or {}
            topic = (
                stuck_context.get("ping_line")
                or analysis.get("ping_line")
                or analysis.get("topic_label")
                or topic
            )
        return self._personalize_with_topic(
            template, user_id, user_data, topic
        )

    async def _classify_segment(
        self, user_id: int, start_param: Optional[str] = None
    ) -> str:
        from bot.followup_segments import start_param_is_ref

        signals = await self.followup_storage.gather_segment_signals(user_id)
        if start_param_is_ref(start_param):
            signals["ref_start"] = True
        return classify_from_signals(signals)

    async def _route_user_to_segment(
        self, user_id: int, segment: str, *, reset_timer: bool = True
    ) -> None:
        """Выставляет status + segment по воронке фазы 1."""
        if segment == SEG_REFUSED:
            await self.followup_storage.set_followup_state(
                user_id,
                self.STATUS_FINAL_REFUSED,
                segment=SEG_REFUSED,
                reset_timer=False,
            )
            return
        if segment == SEG_SENSITIVE:
            await self.followup_storage.set_followup_state(
                user_id,
                self.STATUS_FINAL_SENSITIVE,
                segment=SEG_SENSITIVE,
                reset_timer=False,
            )
            logger.info("Followup: user %s → 997 sensitive (no pings)", user_id)
            return
        if segment == SEG_CART and await self.followup_storage.has_unpaid_order(user_id):
            await self.followup_storage.set_followup_state(
                user_id,
                self.STATUS_WAITING_PAYMENT,
                segment=SEG_CART,
                reset_timer=reset_timer,
            )
            return
        if segment == SEG_ENGAGED:
            if self._stuck_dialog_enabled():
                return
            await self.followup_storage.set_followup_state(
                user_id,
                self.STATUS_WAITING_ENGAGED,
                segment=SEG_ENGAGED,
                reset_timer=reset_timer,
            )
            return
        cold_seg = segment if segment in COLD_SEGMENTS else SEG_ORGANIC_COLD
        await self.followup_storage.set_followup_state(
            user_id,
            self.STATUS_WAITING_ORDER,
            segment=cold_seg,
            reset_timer=reset_timer,
        )

    async def refresh_segment_from_activity(self, user_id: int) -> None:
        """Пересчёт сегмента после сообщения пользователя (messaging)."""
        state = await self.followup_storage.get_followup_state(user_id)
        cur_status = state.get("status") or 0
        if cur_status in (
            self.STATUS_FINAL_PAID,
            self.STATUS_FINAL_SENSITIVE,
            self.STATUS_FINAL_REFUSED,
            self.STATUS_FINAL_BLOCKED,
            self.STATUS_PAYMENT_FOLLOWUP_DONE,
            self.STATUS_ENGAGED_DONE,
            self.STATUS_STUCK_DONE,
        ):
            return

        if cur_status in (
            self.STATUS_WAITING_STUCK,
            self.STATUS_STUCK_PING_SENT,
        ):
            segment = await self._classify_segment(user_id)
            if segment == SEG_REFUSED:
                await self._route_user_to_segment(user_id, SEG_REFUSED)
                return
            if segment == SEG_SENSITIVE:
                await self._route_user_to_segment(user_id, SEG_SENSITIVE)
                return
            await self.followup_storage.set_followup_state(
                user_id,
                self.STATUS_WAITING_ENGAGED,
                segment=SEG_ENGAGED,
                reset_timer=True,
            )
            logger.info(
                "Followup: user %s left stuck_dialog (new message) → engaged 110",
                user_id,
            )
            return

        if await self._has_active_license(user_id):
            await self.followup_storage.set_followup_state(
                user_id, self.STATUS_FINAL_PAID, reset_timer=False
            )
            return
        if await self.followup_storage.has_paid_order(user_id):
            await self.followup_storage.set_followup_state(
                user_id, self.STATUS_FINAL_PAID, reset_timer=False
            )
            return

        segment = await self._classify_segment(user_id)
        last_raw = await self.followup_storage.get_last_meaningful_user_message_text(
            user_id
        )
        last_topic = pick_topic_snippet(last_raw or "") or state.get("last_topic")

        if segment == SEG_REFUSED:
            await self._route_user_to_segment(user_id, SEG_REFUSED)
            return
        if segment == SEG_SENSITIVE:
            await self._route_user_to_segment(user_id, SEG_SENSITIVE)
            return

        if segment == SEG_ENGAGED and cur_status in (
            self.STATUS_WAITING_ORDER,
            self.STATUS_WAITING_ORDER_REMINDED,
            self.STATUS_WAITING_ORDER_FOREVER,
            self.STATUS_NO_FOLLOWUP,
            0,
        ):
            if self._stuck_dialog_enabled():
                return
            await self.followup_storage.set_followup_state(
                user_id,
                self.STATUS_WAITING_ENGAGED,
                segment=SEG_ENGAGED,
                last_topic=last_topic,
                reset_timer=True,
            )
            logger.info("Followup: user %s → engaged 110 (was %s)", user_id, cur_status)
            return

        if segment == SEG_CART and await self.followup_storage.has_unpaid_order(user_id):
            if cur_status not in (
                self.STATUS_WAITING_PAYMENT,
                self.STATUS_WAITING_PAYMENT_REMINDED,
            ):
                await self.followup_storage.set_followup_state(
                    user_id,
                    self.STATUS_WAITING_PAYMENT,
                    segment=SEG_CART,
                    last_topic=last_topic,
                    reset_timer=True,
                )
            else:
                await self.followup_storage.update_segment_meta(
                    user_id, segment=SEG_CART, last_topic=last_topic
                )
            return

        await self.followup_storage.update_segment_meta(
            user_id, segment=segment, last_topic=last_topic
        )

    def _cold_ping_allowed(self, segment: Optional[str], status: int) -> bool:
        if status == self.STATUS_FINAL_SENSITIVE:
            return False
        if segment == SEG_SENSITIVE:
            return False
        if segment in (SEG_ENGAGED, SEG_STUCK_DIALOG):
            return False
        if status in (
            self.STATUS_WAITING_ENGAGED,
            self.STATUS_WAITING_ENGAGED_REMINDED,
            self.STATUS_WAITING_STUCK,
            self.STATUS_STUCK_PING_SENT,
        ):
            return False
        return segment in COLD_SEGMENTS or segment is None

    async def _eligible_for_stuck_dialog(self, user_id: int) -> bool:
        if not self._stuck_dialog_enabled():
            return False
        if await self._has_active_license(user_id):
            return False
        if await self.followup_storage.has_paid_order(user_id):
            return False
        if await self.followup_storage.has_unpaid_order(user_id):
            return False
        if await self.followup_storage.user_has_refusal_in_private_chat(user_id):
            return False
        signals = await self.followup_storage.gather_segment_signals(user_id)
        if signals.get("sensitive"):
            return False
        if signals.get("meaningful_count", 0) < 2:
            return False
        if signals.get("assistant_count", 0) < 1:
            return False
        meta = await self.followup_storage.get_last_private_message_meta(user_id)
        return bool(meta and meta.get("role") == "assistant")

    async def _prepare_stuck_context(self, user_id: int) -> None:
        if user_id in self._stuck_prep_inflight:
            return
        self._stuck_prep_inflight.add(user_id)
        try:
            result = await run_full_stuck_pipeline(
                user_id=user_id,
                user_storage=self.user_storage,
                llm_client=self.llm_client,
                rag_stack=self.rag_stack,
            )
            if result.get("sensitive"):
                await self.followup_storage.set_followup_state(
                    user_id,
                    self.STATUS_FINAL_SENSITIVE,
                    segment=SEG_SENSITIVE,
                    reset_timer=False,
                )
                logger.info("Followup stuck: user %s → 997 (LLM sensitive)", user_id)
                return
            if result.get("error"):
                logger.warning(
                    "Followup stuck prep failed user %s: %s",
                    user_id,
                    result.get("error"),
                )
                return
            await self.followup_storage.set_stuck_context(user_id, result)
            ping = result.get("ping_line") or ""
            if ping:
                await self.followup_storage.update_segment_meta(
                    user_id, segment=SEG_STUCK_DIALOG, last_topic=ping[:80]
                )
            await self.followup_storage.log_stuck_event(
                user_id,
                "followup_stuck_context_ready",
                extra={"chunks": result.get("chunk_count")},
            )
        except Exception as e:
            logger.error("Followup stuck prep user %s: %s", user_id, e)
        finally:
            self._stuck_prep_inflight.discard(user_id)

    async def on_assistant_replied(self, user_id: int) -> None:
        """После ответа ассистента в личке — очередь stuck_dialog (120)."""
        if not await self._eligible_for_stuck_dialog(user_id):
            return
        meta = await self.followup_storage.get_last_private_message_meta(user_id)
        if not meta or meta.get("role") != "assistant":
            return
        assistant_at = meta["created_at"]
        state = await self.followup_storage.get_followup_state(user_id)
        cur = state.get("status") or 0
        if cur in (
            self.STATUS_FINAL_PAID,
            self.STATUS_FINAL_SENSITIVE,
            self.STATUS_FINAL_REFUSED,
            self.STATUS_FINAL_BLOCKED,
            self.STATUS_STUCK_DONE,
            self.STATUS_WAITING_STUCK,
            self.STATUS_STUCK_PING_SENT,
        ):
            return
        await self.followup_storage.set_followup_state(
            user_id,
            self.STATUS_WAITING_STUCK,
            started_at=assistant_at,
            segment=SEG_STUCK_DIALOG,
            last_assistant_at=assistant_at,
            reset_timer=False,
        )
        logger.info(
            "Followup: user %s → stuck_dialog 120 (anchor %s)",
            user_id,
            assistant_at,
        )
        asyncio.create_task(self._prepare_stuck_context(user_id))

    async def _handle_stuck_get_answer(self, callback: CallbackQuery) -> None:
        from bot.services.stuck_dialog_pipeline import compose_answer_from_cached_context

        user_id = callback.from_user.id
        state = await self.followup_storage.get_followup_state(user_id)
        status = state.get("status") or 0
        if status not in (
            self.STATUS_STUCK_PING_SENT,
            self.STATUS_WAITING_STUCK,
        ):
            await callback.answer(followup_txt.stuck_callback_stale_alert, show_alert=True)
            return

        stuck_ctx = state.get("stuck_context") or {}
        if stuck_ctx.get("answer_delivered"):
            await callback.answer(
                followup_txt.stuck_callback_already_sent_alert, show_alert=True
            )
            return

        await callback.answer()
        try:
            await self.bot.send_message(
                chat_id=user_id,
                text=followup_txt.stuck_building_status,
            )
        except Exception:
            pass

        try:
            answer = await compose_answer_from_cached_context(
                user_id=user_id,
                user_storage=self.user_storage,
                llm_client=self.llm_client,
                stuck_context=stuck_ctx,
                rag_stack=self.rag_stack,
            )
        except Exception as e:
            logger.error("stuck answer failed user %s: %s", user_id, e)
            await self.bot.send_message(
                chat_id=user_id,
                text=followup_txt.stuck_build_failed,
            )
            return

        body, _ = strip_subscribe_cta(answer)
        safe = sanitize_telegram_html(body)
        await self._send_message(user_id, safe, self.STATUS_STUCK_PING_SENT, None, None)

        stuck_ctx["composed_answer"] = answer
        stuck_ctx["answer_delivered"] = True
        await self.followup_storage.set_stuck_context(user_id, stuck_ctx)
        await self.followup_storage.log_stuck_event(
            user_id, "followup_stuck_answer_delivered"
        )

        cta_kb = with_main_menu(
            [
                [
                    InlineKeyboardButton(
                        text=followup_txt.BTN_JOIN_CLUB,
                        callback_data=self.CB_PAYMENT_STUCK_S1,
                        style="success",
                    )
                ]
            ]
        )
        cta_safe = sanitize_telegram_html(self.STUCK_CTA_TEXT)
        await self.bot.send_message(
            chat_id=user_id,
            text=cta_safe,
            parse_mode=ParseMode.HTML,
            reply_markup=cta_kb,
        )
        await self.followup_storage.log_stuck_event(
            user_id, "followup_stuck_cta_sent"
        )
        await self.followup_storage.set_followup_state(
            user_id, self.STATUS_STUCK_DONE, segment=SEG_STUCK_DIALOG, reset_timer=False
        )
        logger.info("Followup stuck: answer+CTA delivered user %s → 122", user_id)

    async def initialize(self) -> None:
        """Инициализация фичи"""
        from storage.followup_storage import FollowupStorage
        self.followup_storage = FollowupStorage(self.user_storage)
        
        await self._refresh_cache()

        # Запускаем планировщик для вечерней рассылки
        self.scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
        self.scheduler.add_job(
            self._process_evening_followup,
            CronTrigger(hour=21, minute=4, timezone="Europe/Moscow"),
            id="evening_followup"
        )
        self.scheduler.start()
        logger.info(f"[{self.name}] Планировщик запущен, задача каждый день в 21:04 МСК")
        
        self.is_running = True
        self.task = asyncio.create_task(self._check_loop())
        
        logger.info(f"[{self.name}] Фича инициализирована, фоновый процесс запущен")
    
    async def teardown(self) -> None:
        """Остановка фичи"""
        self.is_running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self.scheduler:
            self.scheduler.shutdown()        
        logger.info(f"[{self.name}] Фича остановлена")
    
    def register_handlers(self, dp) -> None:
        """Регистрация обработчиков"""
        pass

    async def handle_callback(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Кнопки followup (в т.ч. «Получить ответ» для stuck_dialog)."""
        data = (callback.data or "").strip()
        if data == self.CB_STUCK_GET_ANSWER:
            await self._handle_stuck_get_answer(callback)
            return
        await callback.answer()

    # ==================== КЭШ СООБЩЕНИЙ ====================
    
    async def _refresh_cache(self):
        """Обновляет кэш сообщений из БД"""
        try:
            messages = await self.followup_storage.get_all_messages()
            self.message_cache = {
                msg['status']: {
                    'text': msg['message_text'],
                    'delay_minutes': msg['delay_minutes'],
                    'use_ai': msg.get('use_ai', False) or msg['message_text'] == self.AI_GENERATE_MARKER
                }
                for msg in messages
            }
            self.cache_updated_at = datetime.now()
            logger.info(f"🔄 Followup cache refreshed: {len(self.message_cache)} messages")
        except Exception as e:
            logger.error(f"❌ Failed to refresh followup cache: {e}")
    
    async def _ensure_cache_fresh(self):
        """Проверяет, нужно ли обновить кэш"""
        if not self.cache_updated_at:
            await self._refresh_cache()
            return
        
        if (datetime.now() - self.cache_updated_at).total_seconds() > self.cache_ttl:
            await self._refresh_cache()
    
    async def _get_message(self, status: int) -> Optional[Dict[str, Any]]:
        """Получает сообщение для статуса из кэша"""
        await self._ensure_cache_fresh()
        return self.message_cache.get(status)
    
    # ==================== ГЕНЕРАЦИЯ AI СООБЩЕНИЯ ====================
    
    async def _generate_ai_message(self, user_id: int, status: int) -> Optional[str]:
        """Генерирует сообщение через AI-агента из фичи messaging"""
        try:
            messaging = self.feature_manager.get("messaging") if self.feature_manager else None
            
            if not messaging or not messaging.agents_client:
                logger.warning(f"⚠️ AI agent not available for followup to user {user_id}")
                return None
            
            # Формируем промпт в зависимости от статуса
            if status == self.STATUS_WAITING_ORDER:
                prompt = FOLLOWUP_PROMPT_STATUS_101
            elif status == self.STATUS_WAITING_PAYMENT:
                prompt = FOLLOWUP_PROMPT_STATUS_201
            else:
                prompt = FOLLOWUP_PROMPT_GENERIC
            
            response = await messaging.agents_client.run(prompt, user_id)
            
            if response:
                logger.info(f"🤖 AI message generated for user {user_id}, status {status}")
                return response
            else:
                logger.warning(f"⚠️ AI returned empty response for user {user_id}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Failed to generate AI message for user {user_id}: {e}")
            return None
    
    async def _forward_ai_message_to_admin(self, user_id: int, status: int, message_text: str):
        """Отправляет копию AI-сообщения админам"""
        try:
            if not config.ADMIN_CHANNEL_ID:
                logger.warning("⚠️ ADMIN_CHANNEL_ID not configured, skip followup admin copy")
                return

            # Получаем данные пользователя
            user_data = await self.user_storage.get_user(user_id)
            user_name = (
                html.escape(
                    user_data.get("first_name", followup_txt.admin_mirror_default_user_name)
                )
                if user_data
                else followup_txt.admin_mirror_default_user_name
            )
            un = user_data.get("username") if user_data else None
            username_str = (
                html.escape(f"@{un}") if un else followup_txt.admin_mirror_no_username
            )

            body_only, _ = strip_subscribe_cta(message_text)
            escaped_message = sanitize_telegram_html(body_only)

            status_text = (
                followup_txt.admin_mirror_status_waiting_order
                if status == self.STATUS_WAITING_ORDER
                else followup_txt.admin_mirror_status_waiting_payment
            )

            notification_text = followup_txt.admin_ai_followup_mirror_html(
                user_name=user_name,
                username_str=username_str,
                user_id=user_id,
                status_text=status_text,
                escaped_message=escaped_message,
            )

            thread_id = config.ADMIN_DIALOG_THREAD_ID if config.ADMIN_DIALOG_THREAD_ID > 0 else None
            ok = await send_admin_html_message(self.bot, notification_text, thread_id=thread_id)
            if ok:
                logger.info(f"✅ AI followup copy sent to admin for user {user_id}")
            else:
                logger.error("❌ Failed to forward AI followup to admin channel")

        except Exception as e:
            logger.error(f"❌ Error forwarding AI followup to admin: {e}")
    
    # ==================== ФОНОВАЯ ПРОВЕРКА ====================
    
    async def _check_loop(self):
        """Основной цикл проверки"""
        logger.info(f"📡 Followup check loop started, checking every 60 seconds")
        
        while self.is_running:
            try:
                await self._check_users()
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Error in followup check loop: {e}")
                await asyncio.sleep(60)
    
    async def _check_users(self):
        """Проверяет пользователей с активными статусами"""
        async with self._processing_lock:
            users_101 = await self.followup_storage.get_users_by_status(self.STATUS_WAITING_ORDER)
            for user in users_101:
                if user['user_id'] in self._processing_users:
                    continue
                self._processing_users.add(user['user_id'])
                try:
                    await self._process_user(user, self.STATUS_WAITING_ORDER)
                finally:
                    self._processing_users.discard(user['user_id'])
            
            users_120 = await self.followup_storage.get_users_by_status(
                self.STATUS_WAITING_STUCK
            )
            for user in users_120:
                uid = user["user_id"]
                if uid in self._processing_users:
                    continue
                self._processing_users.add(uid)
                try:
                    await self._process_user(user, self.STATUS_WAITING_STUCK)
                finally:
                    self._processing_users.discard(uid)

            users_110 = await self.followup_storage.get_users_by_status(
                self.STATUS_WAITING_ENGAGED
            )
            for user in users_110:
                uid = user["user_id"]
                if uid in self._processing_users:
                    continue
                self._processing_users.add(uid)
                try:
                    await self._process_user(user, self.STATUS_WAITING_ENGAGED)
                finally:
                    self._processing_users.discard(uid)

            for pay_status in (
                self.STATUS_WAITING_PAYMENT,
                self.STATUS_WAITING_PAYMENT_REMINDED,
            ):
                users_pay = await self.followup_storage.get_users_by_status(pay_status)
                for user in users_pay:
                    uid = user["user_id"]
                    if uid in self._processing_users:
                        continue
                    self._processing_users.add(uid)
                    try:
                        await self._process_user(user, pay_status)
                    finally:
                        self._processing_users.discard(uid)
    
    async def _process_user(self, user: Dict[str, Any], status: int):
        """Обрабатывает одного пользователя"""
        user_id = user['user_id']
        started_at = user['started_at']
        segment = user.get("segment")

        if not started_at:
            return

        if status == self.STATUS_FINAL_SENSITIVE or segment == SEG_SENSITIVE:
            return

        if await self._apply_refusal_if_needed(user_id):
            return

        if status == self.STATUS_WAITING_STUCK:
            if not self._stuck_dialog_enabled():
                return
            anchor = user.get("started_at") or user.get("last_assistant_at")
            if not anchor:
                return
            if await self.followup_storage.user_wrote_after(user_id, anchor):
                await self.followup_storage.set_followup_state(
                    user_id,
                    self.STATUS_WAITING_ENGAGED,
                    segment=SEG_ENGAGED,
                    reset_timer=True,
                )
                logger.info(
                    "Followup stuck: user %s wrote again before ping → 110",
                    user_id,
                )
                return
            elapsed = (datetime.now() - anchor).total_seconds() / 60
            if elapsed > self.STUCK_DELAY_MAX_MINUTES:
                await self.followup_storage.try_advance_status(
                    user_id,
                    self.STATUS_WAITING_STUCK,
                    self.STATUS_STUCK_DONE,
                )
                logger.info(
                    "Followup stuck: user %s window expired → 122", user_id
                )
                return
            if elapsed < self.STUCK_DELAY_MIN_MINUTES:
                return

        if status in (
            self.STATUS_WAITING_ORDER,
            self.STATUS_WAITING_ORDER_REMINDED,
        ):
            if not self._cold_ping_allowed(segment, status):
                logger.debug(
                    "Followup: skip cold ping %s user %s segment=%s",
                    status,
                    user_id,
                    segment,
                )
                return
            if await self._should_skip_order_engagement_ping(user_id):
                logger.debug(
                    "Followup: skip %s for user %s (active dialog)",
                    status,
                    user_id,
                )
                return
        
        # Проверяем, не отправляли ли уже недавно
        has_recent_log = await self.followup_storage.has_recent_log(user_id, status, minutes=10)
        if has_recent_log:
            logger.debug(f"⏭️ User {user_id} already got message for status {status} recently")
            return
        
        # Проверяем, прошло ли достаточно времени
        elapsed = (datetime.now() - started_at).total_seconds() / 60
        message_config = await self._get_message(status)
        
        if not message_config:
            logger.warning(f"⚠️ No message config for status {status}")
            return
        
        delay = message_config['delay_minutes']
        
        if elapsed < delay:
            return
        
        # Проверяем, не изменился ли статус
        current_state = await self.followup_storage.get_followup_state(user_id)
        if current_state['status'] != status:
            logger.debug(f"⏭️ User {user_id} status changed from {status} to {current_state['status']}")
            return
        
        # Для статуса 101 и 102 (ожидание заказа)
        if status in [self.STATUS_WAITING_ORDER, self.STATUS_WAITING_ORDER_REMINDED]:
            has_order = await self.followup_storage.has_unpaid_order(user_id)
            if has_order:
                await self.followup_storage.set_followup_state(user_id, self.STATUS_WAITING_PAYMENT)
                logger.info(f"🔄 User {user_id} moved from {status} to 201 (created order)")
                return
        
        # Ожидание оплаты: 201 → 202 → 203
        if status in (
            self.STATUS_WAITING_PAYMENT,
            self.STATUS_WAITING_PAYMENT_REMINDED,
        ):
            has_paid = await self.followup_storage.has_paid_order(user_id)
            if has_paid:
                await self.followup_storage.set_followup_state(user_id, self.STATUS_FINAL_PAID)
                logger.info(f"✅ User {user_id} paid, moved to final status 901")
                return
            if not await self.followup_storage.has_unpaid_order(user_id):
                await self.followup_storage.set_followup_state(
                    user_id, self.STATUS_WAITING_ORDER
                )
                logger.info(
                    "Followup: user %s no pending order, back to 101", user_id
                )
                return

        user_data = await self.user_storage.get_user(user_id)
        state_row = await self.followup_storage.get_followup_state(user_id)
        topic = state_row.get("last_topic")

        template = message_config['text']
        
        if template == self.AI_GENERATE_MARKER:
            # Генерируем через AI
            message_text = await self._generate_ai_message(user_id, status)
            if not message_text:
                # Fallback, если AI не ответил
                message_text = "🙏 Мы заметили, что вы не завершили оформление. Если у вас есть вопросы или нужна помощь — напишите нам!"
            else:
                # Отправляем копию AI-сообщения админам
                await self._forward_ai_message_to_admin(user_id, status, message_text)
        elif status == self.STATUS_WAITING_ENGAGED:
            message_text = self._personalize_with_topic(
                template, user_id, user_data, topic
            )
        elif status == self.STATUS_WAITING_STUCK:
            stuck_ctx = state_row.get("stuck_context")
            if not stuck_ctx or not (stuck_ctx.get("ping_line") or (stuck_ctx.get("analysis") or {}).get("ping_line")):
                if user_id not in self._stuck_prep_inflight:
                    await self._prepare_stuck_context(user_id)
                state_row = await self.followup_storage.get_followup_state(user_id)
                stuck_ctx = state_row.get("stuck_context")
            cur_after_prep = state_row.get("status") or 0
            if cur_after_prep in (
                self.STATUS_FINAL_SENSITIVE,
                self.STATUS_FINAL_REFUSED,
                self.STATUS_FINAL_PAID,
                self.STATUS_FINAL_BLOCKED,
            ):
                logger.debug(
                    "Followup stuck: skip ping user %s (status %s after prep)",
                    user_id,
                    cur_after_prep,
                )
                return
            if stuck_ctx and stuck_ctx.get("sensitive"):
                await self.followup_storage.set_followup_state(
                    user_id, self.STATUS_FINAL_SENSITIVE, segment=SEG_SENSITIVE
                )
                return
            if not stuck_ctx or not (
                stuck_ctx.get("ping_line")
                or (stuck_ctx.get("analysis") or {}).get("ping_line")
            ):
                logger.debug(
                    "Followup stuck: skip ping user %s (no context after prep)",
                    user_id,
                )
                return
            message_text = self._personalize_stuck_ping(
                template, user_id, user_data, stuck_ctx
            )
        else:
            message_text = self._personalize(template, user_id, user_data)

        keyboard = self._keyboard_for_status(status)

        success = await self._send_message(
            user_id, message_text, status, message_config.get("id"), keyboard
        )
        
        if success:
            if status == self.STATUS_WAITING_ORDER:
                next_status = self.STATUS_WAITING_ORDER_REMINDED
                await self.followup_storage.try_advance_status(user_id, status, next_status)
                logger.info(f"📨 Followup sent to user {user_id}, moved from {status} to {next_status}")
            elif status == self.STATUS_WAITING_PAYMENT:
                next_status = self.STATUS_WAITING_PAYMENT_REMINDED
                await self.followup_storage.try_advance_status(user_id, status, next_status)
                logger.info(f"📨 Followup sent to user {user_id}, moved from {status} to {next_status}")
            elif status == self.STATUS_WAITING_PAYMENT_REMINDED:
                await self.followup_storage.try_advance_status(
                    user_id, status, self.STATUS_PAYMENT_FOLLOWUP_DONE
                )
                logger.info(
                    "📨 Followup sent to user %s, payment chain done → 203", user_id
                )
            elif status == self.STATUS_WAITING_ENGAGED:
                await self.followup_storage.try_advance_status(
                    user_id, status, self.STATUS_ENGAGED_DONE
                )
                logger.info(
                    "📨 Engaged followup sent to user %s → 112 (done)", user_id
                )
            elif status == self.STATUS_WAITING_STUCK:
                await self.followup_storage.try_advance_status(
                    user_id, status, self.STATUS_STUCK_PING_SENT
                )
                await self.followup_storage.log_stuck_event(
                    user_id, "followup_stuck_ping_sent"
                )
                logger.info(
                    "📨 Stuck dialog ping sent to user %s → 121", user_id
                )
            else:
                logger.info(f"📨 Followup sent to user {user_id}, staying in status {status}")
        else:
            await self.followup_storage.set_followup_state(user_id, self.STATUS_FINAL_BLOCKED)
            logger.warning(f"🚫 User {user_id} blocked bot, moved to final status 999")
    
    async def _send_message(
        self,
        user_id: int,
        text: str,
        status: int,
        message_id: int = None,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
    ) -> bool:
        """Отправляет сообщение пользователю"""
        try:
            body_only, _ = strip_subscribe_cta(text)
            safe = sanitize_telegram_html(body_only)
            sent_msg = await self.bot.send_message(
                chat_id=user_id,
                text=safe,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
            if self.message_copier and sent_msg:
                row_id = await self.message_copier.save_outgoing(
                    message=sent_msg,
                    source="followup",
                    subtype=str(status),
                )
                if row_id is None:
                    logger.warning(
                        "Followup: not saved to messages uid=%s status=%s mid=%s",
                        user_id,
                        status,
                        sent_msg.message_id,
                    )
            delivered = True
            error = None
            
        except Exception as e:
            delivered = False
            error = str(e)
            error_lower = error.lower()
            is_unreachable = (
                "bot was blocked" in error_lower
                or "blocked by the user" in error_lower
                or "user is deactivated" in error_lower
                or "chat not found" in error_lower
                or (
                    "forbidden" in error_lower
                    and ("block" in error_lower or "bot" in error_lower)
                )
            )
            if is_unreachable:
                logger.warning(
                    "Followup не доставлен user=%s (бот заблокирован / чат недоступен): %s",
                    user_id,
                    e,
                )
                await self._mark_user_inactive(user_id)
            else:
                logger.error("❌ Failed to send followup to %s: %s", user_id, e)
        
        await self.followup_storage.log_send(user_id, status, message_id, delivered, error)
        
        return delivered
    
    # ==================== ВНЕШНИЕ МЕТОДЫ ДЛЯ ТРИГГЕРОВ ====================
    
    async def on_start(
        self,
        user_id: int,
        *,
        is_new_user: bool = False,
        start_param: Optional[str] = None,
    ) -> None:
        """Старт цепочки дожима (только новый пользователь или статус 0)."""
        has_active_license = await self._has_active_license(user_id)
        if has_active_license:
            logger.info(f"✅ User {user_id} has active license, no followup needed")
            await self.followup_storage.set_followup_state(
                user_id, self.STATUS_FINAL_PAID, reset_timer=False
            )
            return
        
        has_paid = await self.followup_storage.has_paid_order(user_id)
        if has_paid:
            await self.followup_storage.set_followup_state(
                user_id, self.STATUS_FINAL_PAID, reset_timer=False
            )
            logger.info(f"✅ User {user_id} already paid, followup stopped")
            return

        current = await self.followup_storage.get_followup_state(user_id)
        cur_status = current.get("status") or 0
        if not is_new_user and cur_status not in (0, self.STATUS_NO_FOLLOWUP):
            logger.info(
                "Followup on_start skipped for returning user %s (status=%s)",
                user_id,
                cur_status,
            )
            return

        if await self._apply_refusal_if_needed(user_id):
            return

        segment = await self._classify_segment(user_id, start_param=start_param)
        await self._route_user_to_segment(user_id, segment)
        logger.info(
            "Followup on_start user %s segment=%s is_new=%s",
            user_id,
            segment,
            is_new_user,
        )
    
    async def on_order_created(self, user_id: int) -> None:
        """Вызывается при создании заказа"""
        has_active_license = await self._has_active_license(user_id)
        if has_active_license:
            logger.info(f"✅ User {user_id} has active license, no followup needed")
            await self.followup_storage.set_followup_state(user_id, self.STATUS_FINAL_PAID)
            return
        
        has_paid = await self.followup_storage.has_paid_order(user_id)
        if has_paid:
            await self.followup_storage.set_followup_state(user_id, self.STATUS_FINAL_PAID)
            logger.info(f"✅ User {user_id} already paid, followup stopped")
            return
        
        await self.followup_storage.set_followup_state(
            user_id,
            self.STATUS_WAITING_PAYMENT,
            segment=SEG_CART,
            reset_timer=True,
        )
        logger.info(f"🔄 User {user_id} moved to followup status 201 (order created)")
    
    async def on_payment_success(self, user_id: int) -> None:
        """Вызывается при успешной оплате"""
        await self.followup_storage.set_followup_state(user_id, self.STATUS_FINAL_PAID)
        logger.info(f"✅ User {user_id} payment success, moved to final status 901")
    
    async def on_user_blocked(self, user_id: int) -> None:
        """Вызывается при блокировке бота пользователем"""
        await self.followup_storage.set_followup_state(user_id, self.STATUS_FINAL_BLOCKED)
        logger.info(f"🚫 User {user_id} blocked bot, moved to final status 999")
    
    # ==================== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ====================
    
    async def _has_active_license(self, user_id: int) -> bool:
        """Проверяет, есть ли у пользователя активная лицензия"""
        try:
            async with self.user_storage.get_connection() as conn:
                row = await conn.fetchval("""
                    SELECT EXISTS(
                        SELECT 1 FROM license 
                        WHERE user_id = $1 
                          AND status = 'active'
                          AND expires_at > NOW()
                    )
                """, user_id)
                return row
        except Exception as e:
            logger.error(f"❌ Failed to check active license for user {user_id}: {e}")
            return False

    async def _mark_user_inactive(self, user_id: int) -> None:
        """Помечает пользователя как неактивного (заблокировал бота)"""
        try:
            async with self.user_storage.get_connection() as conn:
                try:
                    await conn.execute("""
                        UPDATE users 
                        SET is_active = FALSE, 
                            updated_at = NOW()
                        WHERE user_id = $1 AND is_active = TRUE
                    """, user_id)
                except Exception:
                    await conn.execute("""
                        UPDATE users 
                        SET is_active = FALSE
                        WHERE user_id = $1 AND is_active = TRUE
                    """, user_id)
                logger.info(f"🚫 User {user_id} marked as inactive (blocked bot)")
        except Exception as e:
            logger.error(f"❌ Failed to mark user {user_id} as inactive: {e}")

    async def _send_stuck_ping_now(self, user_id: int) -> bool:
        """Собирает и отправляет stuck-пинг без ожидания 24–48 ч (для легаси 103)."""
        if not self._stuck_dialog_enabled():
            return False

        state_row = await self.followup_storage.get_followup_state(user_id)
        if (state_row.get("status") or 0) not in (
            self.STATUS_WAITING_STUCK,
            self.STATUS_STUCK_PING_SENT,
        ):
            return False

        stuck_ctx = state_row.get("stuck_context")
        if not stuck_ctx or not (
            stuck_ctx.get("ping_line")
            or (stuck_ctx.get("analysis") or {}).get("ping_line")
        ):
            if user_id not in self._stuck_prep_inflight:
                await self._prepare_stuck_context(user_id)
            state_row = await self.followup_storage.get_followup_state(user_id)
            stuck_ctx = state_row.get("stuck_context")

        cur = state_row.get("status") or 0
        if cur in (
            self.STATUS_FINAL_SENSITIVE,
            self.STATUS_FINAL_REFUSED,
            self.STATUS_FINAL_PAID,
            self.STATUS_FINAL_BLOCKED,
        ):
            return False
        if stuck_ctx and stuck_ctx.get("sensitive"):
            await self.followup_storage.set_followup_state(
                user_id, self.STATUS_FINAL_SENSITIVE, segment=SEG_SENSITIVE
            )
            return False
        if not stuck_ctx or not (
            stuck_ctx.get("ping_line")
            or (stuck_ctx.get("analysis") or {}).get("ping_line")
        ):
            return False

        message_config = await self._get_message(self.STATUS_WAITING_STUCK)
        if not message_config:
            return False

        user_data = await self.user_storage.get_user(user_id)
        message_text = self._personalize_stuck_ping(
            message_config["text"], user_id, user_data, stuck_ctx
        )
        keyboard = self._keyboard_for_status(self.STATUS_WAITING_STUCK)
        success = await self._send_message(
            user_id,
            message_text,
            self.STATUS_WAITING_STUCK,
            message_config.get("id"),
            keyboard,
        )
        if not success:
            return False

        await self.followup_storage.try_advance_status(
            user_id,
            self.STATUS_WAITING_STUCK,
            self.STATUS_STUCK_PING_SENT,
        )
        await self.followup_storage.log_stuck_event(
            user_id, "followup_stuck_ping_sent", extra={"source": "legacy_103"}
        )
        return True

    async def reactivate_legacy_103_user(
        self,
        user_id: int,
        *,
        last_assistant_at: Optional[datetime] = None,
    ) -> str:
        """
        Переводит легаси 103 (с диалогом) в stuck_dialog и шлёт пинг сразу.

        Returns: sent | skipped_* | failed
        """
        if not self._stuck_dialog_enabled():
            return "skipped_no_stuck"

        state = await self.followup_storage.get_followup_state(user_id)
        if (state.get("status") or 0) != self.STATUS_WAITING_ORDER_FOREVER:
            return "skipped_status"

        if await self._has_active_license(user_id):
            return "skipped_license"
        if await self.followup_storage.has_paid_order(user_id):
            return "skipped_paid"
        if await self._apply_refusal_if_needed(user_id):
            return "skipped_refusal"

        signals = await self.followup_storage.gather_segment_signals(user_id)
        if signals.get("sensitive"):
            await self.followup_storage.set_followup_state(
                user_id,
                self.STATUS_FINAL_SENSITIVE,
                segment=SEG_SENSITIVE,
                reset_timer=False,
            )
            return "skipped_sensitive"

        anchor = last_assistant_at or state.get("last_assistant_at")
        if not anchor:
            meta = await self.followup_storage.get_last_private_message_meta(user_id)
            if meta and meta.get("role") == "assistant" and meta.get("created_at"):
                anchor = meta["created_at"]
        if not anchor:
            async with self.user_storage.get_connection() as conn:
                anchor = await conn.fetchval(
                    """
                    SELECT MAX(created_at) FROM messages
                    WHERE user_id = $1 AND chat_type = 'private'
                      AND role = 'assistant' AND deleted_at IS NULL
                    """,
                    user_id,
                )

        last_raw = await self.followup_storage.get_last_meaningful_user_message_text(
            user_id
        )
        last_topic = pick_topic_snippet(last_raw or "") or state.get("last_topic")

        started_at = datetime.now() - timedelta(
            minutes=self.STUCK_DELAY_MIN_MINUTES + 60
        )
        await self.followup_storage.set_followup_state(
            user_id,
            self.STATUS_WAITING_STUCK,
            started_at=started_at,
            segment=SEG_STUCK_DIALOG,
            last_topic=last_topic,
            reset_timer=False,
            last_assistant_at=anchor,
        )

        await self._prepare_stuck_context(user_id)

        after = await self.followup_storage.get_followup_state(user_id)
        st_after = after.get("status") or 0
        if st_after == self.STATUS_FINAL_SENSITIVE:
            return "skipped_sensitive"
        if st_after == self.STATUS_FINAL_REFUSED:
            return "skipped_refusal"
        if st_after == self.STATUS_FINAL_BLOCKED:
            return "skipped_blocked"

        if await self._send_stuck_ping_now(user_id):
            return "sent"
        return "failed"

    async def run_legacy_103_reactivation_batch(
        self, *, batch_size: int
    ) -> Dict[str, int]:
        """Берёт до batch_size кандидатов и выводит в stuck_dialog."""
        stats: Dict[str, int] = {
            "candidates": 0,
            "sent": 0,
            "failed": 0,
            "skipped": 0,
        }
        candidates = await self.user_storage.fetch_legacy_103_dialog_candidates(
            limit=batch_size
        )
        stats["candidates"] = len(candidates)
        if not candidates:
            logger.info("Legacy 103 reactivation: no candidates")
            return stats

        logger.info(
            "Legacy 103 reactivation: processing %s candidates", len(candidates)
        )

        for row in candidates:
            user_id = int(row["user_id"])
            outcome = await self.reactivate_legacy_103_user(
                user_id,
                last_assistant_at=row.get("last_assistant_at"),
            )
            ping_delivered = outcome == "sent"
            if ping_delivered:
                stats["sent"] += 1
            elif outcome.startswith("skipped"):
                stats["skipped"] += 1
            else:
                stats["failed"] += 1

            await self.user_storage.record_legacy_103_reactivation(
                user_id,
                ping_delivered=ping_delivered,
                skip_reason=None if ping_delivered else outcome,
            )
            await asyncio.sleep(0.5)

        remaining = await self.user_storage.count_legacy_103_dialog_remaining()
        logger.info(
            "Legacy 103 reactivation done: sent=%s failed=%s skipped=%s remaining=%s",
            stats["sent"],
            stats["failed"],
            stats["skipped"],
            remaining,
        )
        return stats

    async def _process_evening_followup(self):
        """
        Отправляет сообщение пользователям со статусом 102 (больше суток)
        и переводит их в статус 103.
        Запускается каждый день в 21:04 по Москве.
        """
        try:
            logger.info("📢 Running evening followup (21:04 MSK)")
            
            # Получаем пользователей со статусом 102, у которых started_at > 24 часов
            users_102 = await self.followup_storage.get_users_for_evening_followup()
            
            if not users_102:
                logger.info("📭 No users for evening followup")
                return
            
            # Получаем сообщение для статуса 102 из кэша
            message_config = await self._get_message(self.STATUS_WAITING_ORDER_REMINDED)  # 102
            if not message_config:
                logger.warning("⚠️ No message config for status 102")
                return
            
            evening_text = message_config['text']
            
            logger.info(f"📨 Sending evening followup to {len(users_102)} users")
            
            for user in users_102:
                user_id = user['user_id']
                if user.get("segment") == SEG_SENSITIVE:
                    continue
                st = await self.followup_storage.get_followup_state(user_id)
                if (st.get("status") or 0) == self.STATUS_FINAL_SENSITIVE:
                    continue

                if await self._apply_refusal_if_needed(user_id):
                    continue
                seg = user.get("segment")
                if not self._cold_ping_allowed(seg, self.STATUS_WAITING_ORDER_REMINDED):
                    continue
                if await self._should_skip_order_engagement_ping(user_id):
                    continue

                user_data = await self.user_storage.get_user(user_id)
                evening_body = self._personalize(evening_text, user_id, user_data)

                success = await self._send_message(
                    user_id,
                    evening_body,
                    self.STATUS_WAITING_ORDER_REMINDED,
                    message_config.get("id"),
                )
                
                if success:
                    # Переводим в статус 103
                    await self.followup_storage.try_advance_status(
                        user_id, 
                        self.STATUS_WAITING_ORDER_REMINDED, 
                        self.STATUS_WAITING_ORDER_FOREVER  # 103
                    )
                    logger.info(f"📨 Evening followup sent to user {user_id}, moved to status 103")
                else:
                    # Если не удалось отправить (пользователь заблокировал бота)
                    await self.followup_storage.set_followup_state(user_id, self.STATUS_FINAL_BLOCKED)
                    await self._mark_user_inactive(user_id)
                    logger.warning(f"🚫 User {user_id} blocked bot, moved to final status 999")
                
                await asyncio.sleep(0.5)
            
        except Exception as e:
            logger.error(f"❌ Error in evening followup: {e}")
